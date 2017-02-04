import sys
assert sys.version_info >= (3,6)

def before_feature(context, feature):
    if 'async' in feature.tags:
        from patch_runners import patch
        patch()


def after_feature(context, feature):
    if 'async' in feature.tags:
        # TODO: revert all changes for patcher functions
        pass