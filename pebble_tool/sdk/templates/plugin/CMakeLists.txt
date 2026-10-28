cmake_minimum_required(VERSION 3.20)

# Before project(): the SDK selects the toolchain.
find_package(PebbleSDK REQUIRED HINTS $$ENV{PEBBLE_SDK})

# A plugin's pbw holds only its script (and its config page, if it has one):
# there is nothing to compile.
project(${project_name} NONE)
