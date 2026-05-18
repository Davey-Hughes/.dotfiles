#!/bin/bash

sudo steamos-readonly disable
./paru-setup.sh && ./rustup.sh && ./packages.sh
