#!/bin/bash

# `sudo cat ./fstab >> /etc/fstab` does NOT work: the shell opens the redirect
# before sudo runs, so the append is attempted as the invoking user and fails on
# a root-owned /etc/fstab. tee is the process that must be privileged.
sudo tee -a /etc/fstab < ./fstab > /dev/null
sudo systemctl daemon-reload
sudo mount -a
