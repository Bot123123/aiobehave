import asyncio
from behave import given, then, step
from behave.api.async_step import use_or_create_async_context


@given('First step')
async def first_step(context):
    print("First_step \n")


@then('I have second step with sleep statement for 5 sec')
async def second_step(context):
    print("Second step: start\n")
    await asyncio.sleep(5)
    print("Second step: finish\n")

async def start_bg_checking():
    await asyncio.sleep(1)
    print ("Calling start_bg_checking\n")
    await start_bg_checking()

@then('I start some background checking every 1 sec')
async def start_looping(context):
    async_context = use_or_create_async_context(context)
    async_context.loop.create_task(start_bg_checking())