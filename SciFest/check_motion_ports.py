#!/usr/bin/env python3
"""
Quick verifier to check which port executes MovJ and to compare reported poses.
Run from project root: python3 check_motion_ports.py
"""
import time
from dobot_api import DobotApiDashboard, DobotApiFeedBack, DobotApi

IP = '192.168.0.37'
DASH_PORT = 29999
MOTION_PORT = 30003
FEED_PORT = 30005


def parse_result(response):
    # return first one or two integers from start of response
    import re
    if response is None:
        return None
    m = re.match(r"^\s*(-?\d+)\s*(?:,\s*(-?\d+))?", response)
    if not m:
        return None
    if m.group(2) is not None:
        return [int(m.group(1)), int(m.group(2))]
    return [int(m.group(1))]


def pretty_pose_from_feedback(fb):
    if fb is None:
        return None
    try:
        # Pose may be in fields like QActual, ToolVectorActual, etc. We'll try RobotMode and CurrentCommandId and QActual
        pose = {
            'RobotMode': int(fb['RobotMode'][0]),
            'CurrentCommandId': int(fb['CurrentCommandId'][0]),
        }
        # QActual contains joint positions; ToolVectorActual or TCPForce may be available
        if 'QActual' in fb.dtype.names:
            pose['QActual'] = [float(x) for x in fb['QActual'][0]]
        return pose
    except Exception as e:
        return {'error': str(e)}


def main():
    print('Connecting...')
    dash = DobotApiDashboard(IP, DASH_PORT)
    motion = DobotApiDashboard(IP, MOTION_PORT)
    fb = DobotApiFeedBack(IP, FEED_PORT)

    print('Powering on...')
    print(dash.PowerOn())
    time.sleep(10)  # Power on takes time

    print('Clearing errors...')
    print(dash.ClearError())
    print('Enabling robot...')
    print(dash.EnableRobot())
    time.sleep(1)

    print('Set User coord 0:', dash.User(0))
    time.sleep(0.5)

    print('Initial GetPose (dashboard):', dash.GetPose())
    fbdata = fb.feedBackData()
    if fbdata is not None:
        print('TestValue:', hex(fbdata['TestValue'][0]))
    print('Initial feedback:', pretty_pose_from_feedback(fbdata))

    # Test MovJ via dashboard
    print('\n--- Send MovJ via dashboard ---')
    cmd = [150.0, -350.0, 300.0, 92.0, 67.0, 107.0]
    try:
        resp = dash.MovJ(*cmd, 0)
        print('dash.MovJ response:', resp, ' parsed:', parse_result(resp))
    except Exception as e:
        print('dash.MovJ exception:', e)
    time.sleep(2)
    print('GetPose after dashboard.MovJ:', dash.GetPose())
    time.sleep(10)
    print('Feedback after dashboard.MovJ:', pretty_pose_from_feedback(fb.feedBackData()))

    # Move back to safe
    print('\nMoving back via dashboard to safe position (0,0,300)')
    try:
        resp = dash.MovJ(150, -350.0, 300.0, 92.0, 67.0, 107.0, 0)
        print('dash.MovJ response:', resp, ' parsed:', parse_result(resp))
    except Exception as e:
        print('dash.MovJ exception:', e)
    time.sleep(2)

    # Test MovJ via motion port
    print('\n--- Send MovJ via motion port ---')
    try:
        resp = motion.MovJ(*cmd, 0)
        print('motion.MovJ response:', resp, ' parsed:', parse_result(resp))
    except Exception as e:
        print('motion.MovJ exception:', e)
    time.sleep(2)
    print('GetPose after motion.MovJ (dashboard):', dash.GetPose())
    print('Feedback after motion.MovJ:', pretty_pose_from_feedback(fb.feedBackData()))

    print('\nDone. Please inspect whether the robot moved to the requested positions and which port caused motion.')

if __name__ == '__main__':
    main()
