#!/usr/bin/env python
"""Django command-line entry point for hosted mode."""

import os
import sys


def main():
    module = "hosted.test_settings" if sys.argv[1:2] == ["test"] else "hosted.settings"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", module)
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
