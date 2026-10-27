#
# This file is the default set of rules to build a Pebble plugin: a pbw holding only the plugin's
# script (and its config page, if it has one), with nothing to install on the watch.
#
# Feel free to customize this to your needs.
#
top = '.'
out = 'build'


def options(ctx):
    ctx.load('pebble_sdk')


def configure(ctx):
    ctx.load('pebble_sdk')


def build(ctx):
    ctx.load('pebble_sdk')
    ctx.pbl_bundle(binaries=[])
