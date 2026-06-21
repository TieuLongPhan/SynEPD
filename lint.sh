#!/bin/bash
flake8 synepd/ test/ --max-line-length=120 --extend-ignore=E203,E266,E501,W503,F401,F403,W291,W293,E302,W391,E261
