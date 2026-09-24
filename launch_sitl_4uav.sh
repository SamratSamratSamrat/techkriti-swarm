#!/bin/bash
source /home/satis/venv-ardupilot/bin/activate
export PATH=$PATH:/home/satis/ardupilot/Tools/autotest
pkill -f sim_vehicle.py 2>/dev/null
pkill -f arducopter 2>/dev/null
sleep 2
mkdir -p /home/satis/swarm-defence/sitl_run_3uav
cd /home/satis/swarm-defence/sitl_run_3uav
rm -f sitl_launch.log
sim_vehicle.py -v ArduCopter --count 4 --auto-sysid --no-mavproxy 2>&1 | tee sitl_launch.log
