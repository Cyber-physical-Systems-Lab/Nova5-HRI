#!/usr/bin/env python3
"""
Dobot Robot Verification Script
Tests basic robot functionality: connection, enabling, feedback, and motion commands.
"""

import threading
import time
import re
from dobot_api import DobotApiDashboard, DobotApiFeedBack

class RobotVerifier:
    def __init__(self, ip="192.168.0.37"):
        self.ip = ip
        self.dashboard_port = 29999
        self.motion_port = 30003
        self.feedback_port = 30004
        self.dashboard = None
        self.motion = None
        self.feedback = None
        self.feed_data = None
        self.connected = False

        # Feedback data structure
        class FeedItem:
            def __init__(self):
                self.robot_mode = -1
                self.current_command_id = 0
                self.digital_inputs = -1
                self.digital_outputs = -1

        self.feed_item = FeedItem()

    def log(self, message):
        """Simple logging function"""
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}")

    def parse_result_id(self, response):
        """Parse the response to extract error code and command ID"""
        if "Not Tcp" in response:
            self.log("ERROR: Control Mode Is Not Tcp")
            return None
        matches = re.findall(r'-?\d+', response)
        if matches:
            return [int(num) for num in matches]
        return None

    def connect_robot(self):
        """Connect to robot dashboard and feedback"""
        try:
            self.log("Connecting to robot...")
            self.dashboard = DobotApiDashboard(self.ip, self.dashboard_port)
            self.motion = DobotApiDashboard(self.ip, self.motion_port)
            self.feedback = DobotApiFeedBack(self.ip, self.feedback_port)
            self.connected = True
            self.log("Connected successfully")
            return True
        except Exception as e:
            self.log(f"Connection failed: {str(e)}")
            return False

    def clear_errors(self):
        """Clear any robot errors"""
        if not self.connected:
            return False
        try:
            resp = self.dashboard.ClearError()
            parsed = self.parse_result_id(resp)
            if parsed and parsed[0] == 0:
                self.log("Errors cleared successfully")
                return True
            else:
                self.log(f"Failed to clear errors: {resp}")
                return False
        except Exception as e:
            self.log(f"Clear errors failed: {str(e)}")
            return False

    def enable_robot(self):
        """Enable the robot"""
        if not self.connected:
            return False
        try:
            resp = self.dashboard.EnableRobot()
            parsed = self.parse_result_id(resp)
            if parsed and parsed[0] == 0:
                self.log("Robot enabled successfully")
                return True
            else:
                self.log(f"Failed to enable robot: {resp}")
                return False
        except Exception as e:
            self.log(f"Enable robot failed: {str(e)}")
            return False

    def set_user_coordinate(self, user=0):
        """Set the user coordinate system"""
        if not self.connected:
            return False
        try:
            resp = self.dashboard.User(user)
            parsed = self.parse_result_id(resp)
            if parsed and parsed[0] == 0:
                self.log(f"User coordinate set to {user}")
                return True
            else:
                self.log(f"Failed to set user coordinate: {resp}")
                return False
        except Exception as e:
            self.log(f"Set user coordinate failed: {str(e)}")
            return False

    def set_tool_coordinate(self, tool=0):
        """Set the tool coordinate system"""
        if not self.connected:
            return False
        try:
            resp = self.dashboard.Tool(tool)
            parsed = self.parse_result_id(resp)
            if parsed and parsed[0] == 0:
                self.log(f"Tool coordinate set to {tool}")
                return True
            else:
                self.log(f"Failed to set tool coordinate: {resp}")
                return False
        except Exception as e:
            self.log(f"Set tool coordinate failed: {str(e)}")
            return False

    def start_feedback_thread(self):
        """Start the feedback monitoring thread"""
        def feedback_loop():
            while self.connected:
                try:
                    feed_info = self.feedback.feedBackData()
                    if feed_info:
                        if hex((feed_info['TestValue'][0])) == '0x123456789abcdef':
                            self.feed_item.robot_mode = feed_info['RobotMode'][0]
                            self.feed_item.digital_inputs = feed_info['DigitalInputs'][0]
                            self.feed_item.digital_outputs = feed_info['DigitalOutputs'][0]
                            self.feed_item.current_command_id = feed_info['CurrentCommandId'][0]
                except Exception as e:
                    self.log(f"Feedback error: {str(e)}")
                time.sleep(0.1)

        thread = threading.Thread(target=feedback_loop, daemon=True)
        thread.start()
        self.log("Feedback thread started")

    def wait_for_motion(self, command_response, timeout=10.0):
        """Wait for motion command to complete"""
        parsed = self.parse_result_id(command_response)
        if not parsed or parsed[0] != 0:
            self.log(f"Motion command failed: {command_response}")
            return False

        command_id = parsed[1]
        start_time = time.time()

        while time.time() - start_time < timeout:
            if self.feed_item.robot_mode == 5 and self.feed_item.current_command_id == command_id:
                self.log("Motion completed successfully")
                return True
            time.sleep(0.1)

        self.log("Motion timeout")
        return False

    def test_motion(self, x, y, z, rx=180.0, ry=0.0, rz=180.0):
        """Test a simple motion command"""
        if not self.connected:
            return False

        self.log(f"Testing motion to: x={x}, y={y}, z={z}, rx={rx}, ry={ry}, rz={rz}")

        # Send MovJ command (coordinateMode=0 for pose)
        resp = self.motion.MovJ(x, y, z, rx, ry, rz, 0, user=0, tool=0)
        self.log(f"MovJ response: {resp}")

        # Wait for completion
        return self.wait_for_motion(resp)

    def get_current_pose(self):
        """Get and print the current robot pose"""
        if not self.connected:
            self.log("Not connected")
            return None
        try:
            resp = self.dashboard.GetPose()
            self.log(f"Current pose: {resp}")
            return resp
        except Exception as e:
            self.log(f"Get pose failed: {str(e)}")
            return None

    def get_robot_status(self):
        """Get current robot status"""
        if not self.connected:
            return None

        try:
            mode_resp = self.dashboard.RobotMode()
            error_resp = self.dashboard.GetErrorID()
            mode_parsed = self.parse_result_id(mode_resp)
            error_parsed = self.parse_result_id(error_resp)

            status = {
                'robot_mode': mode_parsed[1] if mode_parsed else -1,
                'error_id': error_parsed[1] if error_parsed else -1,
                'feed_robot_mode': self.feed_item.robot_mode,
                'feed_command_id': self.feed_item.current_command_id
            }
            return status
        except Exception as e:
            self.log(f"Get status failed: {str(e)}")
            return None

    def run_verification(self):
        """Run the complete verification sequence"""
        self.log("Starting robot verification...")

        # Step 1: Connect
        if not self.connect_robot():
            return False

        # Step 2: Clear errors
        if not self.clear_errors():
            return False

        # Step 3: Enable robot
        if not self.enable_robot():
            return False

        # Step 4: Set user coordinate
        if not self.set_user_coordinate(0):
            return False

        # Step 5: Set tool coordinate
        if not self.set_tool_coordinate(0):
            return False

        # Step 6: Start feedback
        self.start_feedback_thread()
        time.sleep(1)  # Let feedback initialize

        # Step 7: Get initial status
        status = self.get_robot_status()
        if status:
            self.log(f"Initial status: {status}")

        # Step 8: Test motion to a safe position
        # Using a position similar to the demo but safe
        test_pos = [200.0,-400.0,200.0,180.0,0.0,180.0]  # Safe test position
        test_pos2 = [150.0, -350.0, 300.0, 92.0, 67.0, 107.0]
        if self.test_motion(*test_pos):
            # Print current pose before waiting
            self.get_current_pose()
            self.log("Motion test PASSED")
        else:
            # Print current pose before waiting
            self.get_current_pose()
            self.log("Motion test FAILED")
            return False

        # Step 9: Test another position
        if self.test_motion(*test_pos2):
            self.log("Second motion test PASSED")
        else:
            self.log("Second motion test FAILED")
            return False

        self.log("All verification tests PASSED!")
        return True

    def cleanup(self):
        """Clean up connections"""
        self.connected = False
        if self.dashboard:
            try:
                self.dashboard.DisableRobot()
                self.log("Robot disabled")
            except:
                pass
        self.log("Verification complete")

def main():
    verifier = RobotVerifier()

    try:
        success = verifier.run_verification()
        if success:
            print("\n✅ VERIFICATION SUCCESSFUL: Robot is working correctly!")
        else:
            print("\n❌ VERIFICATION FAILED: Check logs above for issues.")
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"\nUnexpected error: {str(e)}")
    finally:
        verifier.cleanup()

if __name__ == "__main__":
    main()