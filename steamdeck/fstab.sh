#!/bin/bash

sudo cat ./fstab >> /etc/fstab
sudo systemctl daemon-reload
sudo mount -a
