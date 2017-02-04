import behave.model
from behave.model_core import Status
import time
from behave.matchers import NoMatch
import six
from behave.textutil import text as _text
import traceback
import asyncio

async def steps_arunner(scenario, run_steps, skip_scenario_untested, runner, dry_run_scenario):
    if not skip_scenario_untested:
        for step in scenario.all_steps:
            if run_steps:
                res = await step.run(runner)
                if not res:
                    # -- CASE: Failed or undefined step
                    #    Optionally continue_after_failed_step if enabled.
                    #    But disable run_steps after undefined-step.
                    run_steps = (scenario.continue_after_failed_step and
                                 step.status == Status.failed)
                    failed = True
                    # pylint: disable=protected-access
                    runner.context._set_root_attribute("failed", True)
                    scenario.set_status(Status.failed)
                elif scenario.should_skip:
                    # -- CASE: Step skipped remaining scenario.
                    # assert self.status == Status.skipped
                    run_steps = False
            elif failed or dry_run_scenario:
                # -- SKIP STEPS: After failure/undefined-step occurred.
                # BUT: Detect all remaining undefined steps.
                step.status = Status.skipped
                if dry_run_scenario:
                    # pylint: disable=redefined-variable-type
                    step.status = Status.untested
                found_step_match = runner.step_registry.find_match(step)
                if not found_step_match:
                    step.status = Status.undefined
                    runner.undefined_steps.append(step)
                elif dry_run_scenario:
                    # -- BETTER DIAGNOSTICS: Provide step file location
                    # (when --format=pretty is used).
                    assert step.status == Status.untested
                    for formatter in runner.formatters:
                        # -- EMULATE: Step.run() protocol w/o step execution.
                        formatter.match(found_step_match)
                        formatter.result(step)
            else:
                # -- SKIP STEPS: For disabled scenario.
                # CASES:
                #   * Undefined steps are not detected (by intention).
                #   * Step skipped remaining scenario.
                step.status = Status.skipped

def async_scenario_runner(self, runner):
    self.clear_status()
    self.captured.reset()
    self.hook_failed = False
    failed = False
    skip_scenario_untested = runner.aborted
    run_scenario = self.should_run(runner.config)
    run_steps = run_scenario and not runner.config.dry_run
    dry_run_scenario = run_scenario and runner.config.dry_run
    self.was_dry_run = dry_run_scenario

    runner.context._push()  # pylint: disable=protected-access
    runner.context.scenario = self
    runner.context.tags = set(self.effective_tags)

    hooks_called = False
    if not runner.config.dry_run and run_scenario:
        hooks_called = True
        for tag in self.tags:
            runner.run_hook("before_tag", runner.context, tag)
        runner.run_hook("before_scenario", runner.context, self)
        if self.hook_failed:
            # -- SKIP: Scenario steps and behave like dry_run_scenario
            failed = True

        # -- RE-EVALUATE SHOULD-RUN STATE:
        # Hook may call scenario.mark_skipped() to exclude it.
        skip_scenario_untested = self.hook_failed or runner.aborted
        run_scenario = self.should_run()
        run_steps = run_scenario and not runner.config.dry_run

    if run_scenario or runner.config.show_skipped:
        for formatter in runner.formatters:
            formatter.scenario(self)

    # TODO: Reevaluate location => Move in front of hook-calls
    runner.setup_capture()

    if run_scenario or runner.config.show_skipped:
        for step in self:
            for formatter in runner.formatters:
                formatter.step(step)


    loop = asyncio.get_event_loop()
    loop.run_until_complete(steps_arunner(self, run_steps, skip_scenario_untested, runner, dry_run_scenario))

    self.clear_status()  # -- ENFORCE: compute_status() after run.
    if not run_scenario and not self.steps:
        # -- SPECIAL CASE: Scenario without steps.
        self.set_status(Status.skipped)

    if hooks_called:
        runner.run_hook("after_scenario", runner.context, self)
        for tag in self.tags:
            runner.run_hook("after_tag", runner.context, tag)
        if self.hook_failed:
            failed = True
            self.set_status(Status.failed)

    # -- CAPTURED-OUTPUT:
    store_captured = (runner.config.junit or self.status == Status.failed)
    if store_captured:
        self.captured = runner.capture_controller.captured

    runner.teardown_capture()
    runner.context._pop()  # pylint: disable=protected-access
    return failed


async def async_step_run(self, runner, quiet=False, capture=True):
    # pylint: disable=too-many-branches, too-many-statements
    # -- RESET: Run-time information.
    # self.status = Status.untested
    # self.hook_failed = False
    self.reset()

    match = runner.step_registry.find_match(self)
    if match is None:
        runner.undefined_steps.append(self)
        if not quiet:
            for formatter in runner.formatters:
                formatter.match(NoMatch())

        self.status = Status.undefined
        if not quiet:
            for formatter in runner.formatters:
                formatter.result(self)
        return False

    keep_going = True
    error = ""

    if not quiet:
        for formatter in runner.formatters:
            formatter.match(match)

    if capture:
        runner.start_capture()

    skip_step_untested = False
    runner.run_hook("before_step", runner.context, self)
    if self.hook_failed:
        skip_step_untested = True

    start = time.time()
    if not skip_step_untested:
        try:
            # -- ENSURE:
            #  * runner.context.text/.table attributes are reset (#66).
            #  * Even EMPTY multiline text is available in context.
            runner.context.text = self.text
            runner.context.table = self.table
            await match.run(runner.context)
            if self.status == Status.untested:
                # -- NOTE: Executed step may have skipped scenario and itself.
                # pylint: disable=redefined-variable-type
                self.status = Status.passed
        except KeyboardInterrupt as e:
            runner.aborted = True
            error = "ABORTED: By user (KeyboardInterrupt)."
            self.status = Status.failed
            self.store_exception_context(e)
        except AssertionError as e:
            self.status = Status.failed
            self.store_exception_context(e)
            if e.args:
                message = _text(e)
                error = "Assertion Failed: " + message
            else:
                # no assertion text; format the exception
                error = _text(traceback.format_exc())
        except Exception as e:  # pylint: disable=broad-except
            self.status = Status.failed
            error = _text(traceback.format_exc())
            self.store_exception_context(e)

    self.duration = time.time() - start
    runner.run_hook("after_step", runner.context, self)
    if self.hook_failed:
        self.status = Status.failed

    if capture:
        runner.stop_capture()

    # flesh out the failure with details
    store_captured_always = False  # PREPARED
    store_captured = self.status == Status.failed or store_captured_always
    if self.status == Status.failed:
        assert isinstance(error, six.text_type)
        if capture:
            # -- CAPTURE-ONLY: Non-nested step failures.
            self.captured = runner.capture_controller.captured
            error2 = self.captured.make_report()
            if error2:
                error += "\n" + error2
        self.error_message = error
        keep_going = False
    elif store_captured and capture:
        self.captured = runner.capture_controller.captured

    if not quiet:
        for formatter in runner.formatters:
            formatter.result(self)

    return keep_going


def patch():
    behave.model.Scenario.run = async_scenario_runner
    behave.model.Step.run = async_step_run
