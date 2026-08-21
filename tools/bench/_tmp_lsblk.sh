#!/bin/sh
lsblk -o NAME,TYPE,TRAN,SIZE,MODEL
echo ---
ls -la /dev/sd* /dev/vd* /dev/nvme* /dev/xvd* 2>/dev/null || true
echo ---
cat /proc/partitions