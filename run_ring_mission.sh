#!/bin/bash
source /home/satis/venv-ardupilot/bin/activate
mkdir -p /home/satis/techkriti-swarm/logs
cd /home/satis/swarm-defence
python3 -u tools/ring.py 2>&1 | tee /home/satis/swarm-defence/sitl_run_3uav/commander_stdout.log
