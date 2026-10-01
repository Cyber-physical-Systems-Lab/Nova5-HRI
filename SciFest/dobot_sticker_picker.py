#!/usr/bin/env python3
"""
Dobot Industrial Arm GUI for Adaptive Sticker Picking
Handles pickup from a decreasing stack and drop operations with GUI controls
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import time
from datetime import datetime
import json
import os
import re
from dobot_api import DobotApiDashboard, DobotApiFeedBack

try:
    from gpiozero import DigitalOutputDevice
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("Warning: gpiozero not available. Pump control will be disabled.")

class DobotStickerPicker:
    def __init__(self, root):
        self.root = root
        self.root.title("Dobot Industrial Arm - Adaptive Sticker Picker")
        self.root.geometry("800x700")
        
        # Robot connection
        self.dashboard_api = None
        self.motion_api = None
        self.feedback_api = None
        self.connected = False
        
        # Stack parameters
        self.initial_stack_height = 100.0  # 10cm in mm
        self.current_stack_height = self.initial_stack_height
        self.sticker_thickness = 0.5  # 0.5mm per sticker (adjust as needed)
        self.pickup_count = 0
        self.safety_offset = 20.0  # 2cm safety offset above stack
        
        # Position tracking
        self.pickup_pos = {'x': 0, 'y': 0, 'z': 0}
        self.drop_pos = {'x': 0, 'y': 0, 'z': 0}
        
        # Pump control (Raspberry Pi GPIO)
        self.pump_pin = 20  # BCM pin 20 - main solenoid valve
        self.vent_pin = 21  # BCM pin 21 - vent/bleed valve
        self.pump_device = None
        self.vent_device = None
        self.pump_state = False  # False = OFF, True = ON
        
        self.setup_gui()
        
    def setup_gui(self):
        """Setup the main GUI interface"""
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Connection frame
        self.setup_connection_frame(main_frame)
        
        # Position settings frame
        self.setup_position_frame(main_frame)
        
        # Stack settings frame
        self.setup_stack_frame(main_frame)
        
        # Control buttons frame
        self.setup_control_frame(main_frame)
        
        # Status and log frame
        self.setup_status_frame(main_frame)
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        
    def setup_connection_frame(self, parent):
        """Setup robot connection controls"""
        conn_frame = ttk.LabelFrame(parent, text="Robot Connection", padding="5")
        conn_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        ttk.Label(conn_frame, text="IP Address:").grid(row=0, column=0, sticky=tk.W)
        self.ip_var = tk.StringVar(value="192.168.1.6")
        ttk.Entry(conn_frame, textvariable=self.ip_var, width=15).grid(row=0, column=1, sticky=tk.W)
        
        ttk.Label(conn_frame, text="Dashboard Port:").grid(row=0, column=2, sticky=tk.W, padx=(20,0))
        self.dash_port_var = tk.StringVar(value="29999")
        ttk.Entry(conn_frame, textvariable=self.dash_port_var, width=8).grid(row=0, column=3, sticky=tk.W)
        
        ttk.Label(conn_frame, text="Motion Port:").grid(row=0, column=4, sticky=tk.W, padx=(20,0))
        self.motion_port_var = tk.StringVar(value="30003")
        ttk.Entry(conn_frame, textvariable=self.motion_port_var, width=8).grid(row=0, column=5, sticky=tk.W)
        
        ttk.Label(conn_frame, text="Feedback Port:").grid(row=0, column=6, sticky=tk.W, padx=(20,0))
        self.feedback_port_var = tk.StringVar(value="30004")
        ttk.Entry(conn_frame, textvariable=self.feedback_port_var, width=8).grid(row=0, column=7, sticky=tk.W)
        
        self.connect_btn = ttk.Button(conn_frame, text="Connect", command=self.connect_robot)
        self.connect_btn.grid(row=0, column=8, padx=(20,0))
        
        self.status_label = ttk.Label(conn_frame, text="Disconnected", foreground="red")
        self.status_label.grid(row=0, column=9, padx=(10,0))
        
    def setup_position_frame(self, parent):
        """Setup position input controls"""
        pos_frame = ttk.LabelFrame(parent, text="Position Settings", padding="5")
        pos_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        # Pickup position
        ttk.Label(pos_frame, text="Pickup Position (mm):", font=("Arial", 10, "bold")).grid(row=0, column=0, columnspan=3, sticky=tk.W)
        
        ttk.Label(pos_frame, text="X:").grid(row=1, column=0, sticky=tk.W)
        self.pickup_x_var = tk.StringVar(value="-300")
        ttk.Entry(pos_frame, textvariable=self.pickup_x_var, width=10).grid(row=1, column=1, sticky=tk.W)
        
        ttk.Label(pos_frame, text="Y:").grid(row=1, column=2, sticky=tk.W, padx=(20,0))
        self.pickup_y_var = tk.StringVar(value="-400")
        ttk.Entry(pos_frame, textvariable=self.pickup_y_var, width=10).grid(row=1, column=3, sticky=tk.W)
        
        ttk.Label(pos_frame, text="Z:").grid(row=1, column=4, sticky=tk.W, padx=(20,0))
        self.pickup_z_var = tk.StringVar(value="100")
        ttk.Entry(pos_frame, textvariable=self.pickup_z_var, width=10).grid(row=1, column=5, sticky=tk.W)
        
        # Drop position
        ttk.Label(pos_frame, text="Drop Position (mm):", font=("Arial", 10, "bold")).grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(10,0))
        
        ttk.Label(pos_frame, text="X:").grid(row=3, column=0, sticky=tk.W)
        self.drop_x_var = tk.StringVar(value="300")
        ttk.Entry(pos_frame, textvariable=self.drop_x_var, width=10).grid(row=3, column=1, sticky=tk.W)
        
        ttk.Label(pos_frame, text="Y:").grid(row=3, column=2, sticky=tk.W, padx=(20,0))
        self.drop_y_var = tk.StringVar(value="500")
        ttk.Entry(pos_frame, textvariable=self.drop_y_var, width=10).grid(row=3, column=3, sticky=tk.W)
        
        ttk.Label(pos_frame, text="Z:").grid(row=3, column=4, sticky=tk.W, padx=(20,0))
        self.drop_z_var = tk.StringVar(value="100")
        ttk.Entry(pos_frame, textvariable=self.drop_z_var, width=10).grid(row=3, column=5, sticky=tk.W)
        
    def setup_stack_frame(self, parent):
        """Setup stack height and sticker settings"""
        stack_frame = ttk.LabelFrame(parent, text="Stack Settings", padding="5")
        stack_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        ttk.Label(stack_frame, text="Initial Stack Height (mm):").grid(row=0, column=0, sticky=tk.W)
        self.initial_height_var = tk.StringVar(value="100")
        ttk.Entry(stack_frame, textvariable=self.initial_height_var, width=10).grid(row=0, column=1, sticky=tk.W)
        
        ttk.Label(stack_frame, text="Sticker Thickness (mm):").grid(row=0, column=2, sticky=tk.W, padx=(20,0))
        self.thickness_var = tk.StringVar(value="0.5")
        ttk.Entry(stack_frame, textvariable=self.thickness_var, width=10).grid(row=0, column=3, sticky=tk.W)
        
        ttk.Label(stack_frame, text="Safety Offset (mm):").grid(row=0, column=4, sticky=tk.W, padx=(20,0))
        self.offset_var = tk.StringVar(value="20")
        ttk.Entry(stack_frame, textvariable=self.offset_var, width=10).grid(row=0, column=5, sticky=tk.W)
        
        # Current status
        ttk.Label(stack_frame, text="Current Stack Height:").grid(row=1, column=0, sticky=tk.W, pady=(10,0))
        self.current_height_label = ttk.Label(stack_frame, text="100.0 mm", foreground="blue")
        self.current_height_label.grid(row=1, column=1, sticky=tk.W, pady=(10,0))
        
        ttk.Label(stack_frame, text="Pickup Count:").grid(row=1, column=2, sticky=tk.W, padx=(20,0), pady=(10,0))
        self.pickup_count_label = ttk.Label(stack_frame, text="0", foreground="blue")
        self.pickup_count_label.grid(row=1, column=3, sticky=tk.W, pady=(10,0))
        
        ttk.Button(stack_frame, text="Reset Stack", command=self.reset_stack).grid(row=1, column=4, padx=(20,0), pady=(10,0))
        
    def setup_control_frame(self, parent):
        """Setup main control buttons"""
        control_frame = ttk.LabelFrame(parent, text="Operation Controls", padding="5")
        control_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        self.home_btn = ttk.Button(control_frame, text="Home Robot", command=self.home_robot)
        self.home_btn.grid(row=0, column=0, padx=5)
        
        self.enable_btn = ttk.Button(control_frame, text="Enable Robot", command=self.enable_robot)
        self.enable_btn.grid(row=0, column=1, padx=5)
        
        self.test_pickup_btn = ttk.Button(control_frame, text="Test Pickup Position", command=self.test_pickup_position)
        self.test_pickup_btn.grid(row=0, column=2, padx=5)
        
        self.test_drop_btn = ttk.Button(control_frame, text="Test Drop Position", command=self.test_drop_position)
        self.test_drop_btn.grid(row=0, column=3, padx=5)
        
        # Main operation buttons
        self.single_pick_btn = ttk.Button(control_frame, text="Single Pick & Place", 
                                         command=self.single_pick_and_place, style="Accent.TButton")
        self.single_pick_btn.grid(row=1, column=0, columnspan=2, padx=5, pady=5, sticky=(tk.W, tk.E))
        
        self.continuous_btn = ttk.Button(control_frame, text="Start Continuous", 
                                       command=self.start_continuous_operation)
        self.continuous_btn.grid(row=1, column=2, padx=5, pady=5, sticky=(tk.W, tk.E))
        
        self.stop_btn = ttk.Button(control_frame, text="EMERGENCY STOP", 
                                 command=self.emergency_stop_operation, style="Danger.TButton")
        self.stop_btn.grid(row=1, column=3, padx=5, pady=5, sticky=(tk.W, tk.E))
        
        # Pump control buttons
        self.pump_on_btn = ttk.Button(control_frame, text="Pump ON", command=self.pump_on)
        self.pump_on_btn.grid(row=2, column=0, padx=5, pady=5)
        
        self.pump_off_btn = ttk.Button(control_frame, text="Pump OFF", command=self.pump_off)
        self.pump_off_btn.grid(row=2, column=1, padx=5, pady=5)
        
        self.pump_toggle_btn = ttk.Button(control_frame, text="Toggle Pump", command=self.toggle_pump)
        self.pump_toggle_btn.grid(row=2, column=2, padx=5, pady=5)
        
        # Get current position button
        self.get_pos_btn = ttk.Button(control_frame, text="Get Current Position", command=self.get_current_position)
        self.get_pos_btn.grid(row=0, column=4, padx=5)
        
        self.clear_error_btn = ttk.Button(control_frame, text="Clear Error", command=self.clear_error)
        self.clear_error_btn.grid(row=0, column=5, padx=5)
        
        self.disable_btn = ttk.Button(control_frame, text="Disable Robot", command=self.disable_robot)
        self.disable_btn.grid(row=0, column=6, padx=5)
        
        # Configure button styles
        style = ttk.Style()
        style.configure("Accent.TButton", foreground="white", background="green")
        style.configure("Danger.TButton", foreground="white", background="red")
        
    def setup_status_frame(self, parent):
        """Setup status display and logging"""
        status_frame = ttk.LabelFrame(parent, text="Status & Log", padding="5")
        status_frame.grid(row=4, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        
        self.log_text = scrolledtext.ScrolledText(status_frame, width=80, height=15)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(0, weight=1)
        
    def log_message(self, message):
        """Add message to log with timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"
        self.log_text.insert(tk.END, log_entry)
        self.log_text.see(tk.END)
        self.root.update_idletasks()
        
    def connect_robot(self):
        """Connect to the Dobot robot"""
        if self.connected:
            self.disconnect_robot()
            return
            
        try:
            ip = self.ip_var.get()
            dash_port = int(self.dash_port_var.get())
            motion_port = int(self.motion_port_var.get())
            feedback_port = int(self.feedback_port_var.get())
            
            self.log_message(f"Connecting to robot at {ip}...")
            
            # Connect to dashboard API (control commands)
            self.dashboard_api = DobotApiDashboard(ip, dash_port)
            
            # Connect to motion API (movement commands)
            self.motion_api = DobotApiDashboard(ip, motion_port)
            
            # Connect to feedback API for position monitoring
            self.feedback_api = DobotApiFeedBack(ip, feedback_port)
            
            # Test connection
            self.dashboard_api.ClearError()
            
            # Initialize pump control if GPIO available
            self.initialize_pump_control()
            
            self.connected = True
            self.connect_btn.config(text="Disconnect")
            self.status_label.config(text="Connected", foreground="green")
            self.log_message("Successfully connected to robot")
            
        except Exception as e:
            self.log_message(f"Connection failed: {str(e)}")
            messagebox.showerror("Connection Error", f"Failed to connect to robot:\n{str(e)}")
            
    def disconnect_robot(self):
        """Disconnect from robot"""
        try:
            if self.dashboard_api:
                self.dashboard_api.DisableRobot()
                self.dashboard_api.close()
            if self.motion_api:
                self.motion_api.close()
            if self.feedback_api:
                self.feedback_api.close()
                
            # Cleanup pump control
            self.cleanup_pump_control()
                
            self.connected = False
            self.connect_btn.config(text="Connect")
            self.status_label.config(text="Disconnected", foreground="red")
            self.log_message("Disconnected from robot")
            
        except Exception as e:
            self.log_message(f"Disconnect error: {str(e)}")
            
    def update_stack_height(self):
        """Update current stack height based on pickup count"""
        self.current_stack_height = max(0, self.initial_stack_height - (self.pickup_count * self.sticker_thickness))
        self.current_height_label.config(text=f"{self.current_stack_height:.1f} mm")
        self.pickup_count_label.config(text=str(self.pickup_count))
        
    def reset_stack(self):
        """Reset stack parameters"""
        self.initial_stack_height = float(self.initial_height_var.get())
        self.sticker_thickness = float(self.thickness_var.get())
        self.safety_offset = float(self.offset_var.get())
        self.pickup_count = 0
        self.current_stack_height = self.initial_stack_height
        self.update_stack_height()
        self.log_message("Stack parameters reset")
        
    def enable_robot(self):
        """Enable the robot"""
        if not self.connected:
            messagebox.showerror("Error", "Robot not connected!")
            return
            
        try:
            self.dashboard_api.ClearError()
            self.dashboard_api.EnableRobot()
            time.sleep(1)
            self.log_message("Robot enabled")
        except Exception as e:
            self.log_message(f"Enable error: {str(e)}")
            messagebox.showerror("Error", f"Failed to enable robot:\n{str(e)}")
            
    def clear_error(self):
        """Clear robot errors"""
        if not self.connected:
            messagebox.showerror("Error", "Robot not connected!")
            return
            
        try:
            self.dashboard_api.ClearError()
            self.log_message("Robot errors cleared")
        except Exception as e:
            self.log_message(f"Clear error failed: {str(e)}")
            messagebox.showerror("Error", f"Failed to clear robot errors:\n{str(e)}")
            
    def disable_robot(self):
        """Disable the robot"""
        if not self.connected:
            messagebox.showerror("Error", "Robot not connected!")
            return
            
        try:
            self.dashboard_api.DisableRobot()
            self.log_message("Robot disabled")
        except Exception as e:
            self.log_message(f"Disable error: {str(e)}")
            messagebox.showerror("Error", f"Failed to disable robot:\n{str(e)}")
            
    def home_robot(self):
        """Move robot to home position"""
        if not self.connected:
            messagebox.showerror("Error", "Robot not connected!")
            return
            
        try:
            self.log_message("Moving robot to home position...")
            # Move to a safe home position (coordinateMode 0 = pose mode)
            resp = self.motion_api.MovJ(-251, -372, 220, 180, 0, 180, 0)
            # wait for motion to complete if feedback available
            try:
                self._wait_for_motion(resp)
            except Exception:
                pass
            self.log_message("Robot moved to home position")
        except Exception as e:
            self.log_message(f"Home error: {str(e)}")
            messagebox.showerror("Error", f"Failed to home robot:\n{str(e)}")
            
    def get_adaptive_pickup_height(self):
        """Calculate the current pickup height based on stack level"""
        base_z = float(self.pickup_z_var.get())
        return base_z + self.current_stack_height
        
    def get_current_position(self):
        """Get and display current robot position"""
        if not self.connected:
            messagebox.showerror("Error", "Robot not connected!")
            return
            
        try:
            # Get current pose from dashboard API
            pose_str = self.dashboard_api.GetPose()
            self.log_message(f"Current robot position: {pose_str}")
            
            # Parse the pose string to extract coordinates if needed
            # Format is typically like: "{x,y,z,rx,ry,rz}"
            messagebox.showinfo("Current Position", f"Robot Position:\n{pose_str}")
            
        except Exception as e:
            self.log_message(f"Get position error: {str(e)}")
            messagebox.showerror("Error", f"Failed to get current position:\n{str(e)}")

    def _parse_result_id(self, resp_text):
        """Return parsed [error, command_id] or None if cannot parse"""
        try:
            nums = re.findall(r'-?\d+', resp_text)
            nums = [int(n) for n in nums]
            if len(nums) == 0:
                return None
            if len(nums) == 1:
                return [nums[0], None]
            return [nums[0], nums[1]]
        except Exception:
            return None

    def _wait_for_motion(self, resp_text, timeout=10.0):
        """Waits until the motion command indicated by resp_text finishes.

        Uses feedback_api (CurrentCommandId + RobotMode) when available. Falls
        back to a simple sleep if feedback not configured. Raises on timeout.
        """
        parsed = self._parse_result_id(resp_text)
        if parsed is None:
            # can't parse, fallback
            time.sleep(0.1)
            return

        error_code, cmd_id = parsed
        if error_code != 0:
            # command failed - nothing to wait for
            return

        if not self.feedback_api:
            # No feedback available - short pause
            time.sleep(0.1)
            return

        start = time.time()
        while True:
            if time.time() - start > timeout:
                raise TimeoutError("Motion wait timed out")
            try:
                fb = self.feedback_api.feedBackData()
                if fb is None:
                    time.sleep(0.01)
                    continue
                # fb is a numpy structured array; extract fields
                robot_mode = int(fb['RobotMode'][0])
                current_cmd = int(fb['CurrentCommandId'][0])
                # ROBOT_MODE_RUNNING == 7, ROBOT_MODE_ENABLE == 5, ROBOT_MODE_IDLE may be 0/other
                # consider motion finished when robot is enabled/idle and current command matches
                if (robot_mode == 5 or robot_mode == 8 or robot_mode == 0 or robot_mode == 7) and cmd_id is not None:
                    # when the feedback reports the current command id equals the command we sent,
                    # and the mode indicates not actively running (5 enable/idle or 8 single move),
                    # we treat as finished. Some controllers set mode to 7 while running, so ensure
                    # we break only when it's not running or when it reports the same command id after running.
                    if robot_mode != 7 and current_cmd == cmd_id:
                        return
                    # if robot_mode == 7 (running) but current_cmd == cmd_id, still wait until it leaves running
                    if robot_mode == 7 and current_cmd != cmd_id:
                        # another command started, consider previous finished
                        return
                time.sleep(0.01)
            except Exception:
                # transient error reading feedback - retry
                time.sleep(0.01)
                continue
            
    def validate_positions(self):
        """Validate pickup and drop positions before operation"""
        try:
            # Validate pickup position
            pickup_x = float(self.pickup_x_var.get())
            pickup_y = float(self.pickup_y_var.get())
            pickup_z = float(self.pickup_z_var.get())
            
            # Validate drop position
            drop_x = float(self.drop_x_var.get())
            drop_y = float(self.drop_y_var.get())
            drop_z = float(self.drop_z_var.get())
            
            # Basic range validation (adjust these limits based on your robot's workspace)
            workspace_limits = {
                'x_min': -800, 'x_max': 800,
                'y_min': -800, 'y_max': 800,
                'z_min': 0, 'z_max': 600
            }
            
            positions = [
                ("Pickup X", pickup_x), ("Pickup Y", pickup_y), ("Pickup Z", pickup_z),
                ("Drop X", drop_x), ("Drop Y", drop_y), ("Drop Z", drop_z)
            ]
            
            for name, value in positions:
                coord = name.split()[1].lower()
                if coord in ['x']:
                    if not (workspace_limits['x_min'] <= value <= workspace_limits['x_max']):
                        raise ValueError(f"{name} ({value}) is outside workspace limits [{workspace_limits['x_min']}, {workspace_limits['x_max']}]")
                elif coord in ['y']:
                    if not (workspace_limits['y_min'] <= value <= workspace_limits['y_max']):
                        raise ValueError(f"{name} ({value}) is outside workspace limits [{workspace_limits['y_min']}, {workspace_limits['y_max']}]")
                elif coord in ['z']:
                    if not (workspace_limits['z_min'] <= value <= workspace_limits['z_max']):
                        raise ValueError(f"{name} ({value}) is outside workspace limits [{workspace_limits['z_min']}, {workspace_limits['z_max']}]")
                        
            return True
            
        except ValueError as e:
            self.log_message(f"Position validation error: {str(e)}")
            messagebox.showerror("Position Error", str(e))
            return False
        except Exception as e:
            self.log_message(f"Validation error: {str(e)}")
            messagebox.showerror("Error", f"Position validation failed:\n{str(e)}")
            return False
        
    def check_robot_status(self):
        """Return True if robot appears connected and enabled.

        Uses dashboard_api and feedback_api where available.
        """
        if not self.connected or not self.dashboard_api:
            return False
        try:
            # Try to query current command id and/or robot mode from feedback
            if self.feedback_api:
                fb = self.feedback_api.feedBackData()
                if fb is None:
                    return True
                # RobotMode 5 = enabled/idle, 7 = running, 9 = error
                robot_mode = int(fb['RobotMode'][0])
                if robot_mode == 9 or robot_mode == 3:
                    return False
            return True
        except Exception:
            return True
        
    def test_pickup_position(self):
        """Move to pickup position for testing"""
        if not self.connected:
            messagebox.showerror("Error", "Robot not connected!")
            return
            
        try:
            x = float(self.pickup_x_var.get())
            y = float(self.pickup_y_var.get())
            z = self.get_adaptive_pickup_height() + self.safety_offset
            
            self.log_message(f"Moving to pickup test position: X={x}, Y={y}, Z={z}")
            resp = self.motion_api.MovJ(x, y, z, 180, 0, 180, 0)
            try:
                self._wait_for_motion(resp)
            except Exception:
                pass
            self.log_message("Moved to pickup test position")
            
        except Exception as e:
            self.log_message(f"Test pickup error: {str(e)}")
            messagebox.showerror("Error", f"Failed to move to pickup position:\n{str(e)}")
            
    def test_drop_position(self):
        """Move to drop position for testing"""
        if not self.connected:
            messagebox.showerror("Error", "Robot not connected!")
            return
            
        try:
            x = float(self.drop_x_var.get())
            y = float(self.drop_y_var.get())
            z = float(self.drop_z_var.get()) + self.safety_offset
            
            self.log_message(f"Moving to drop test position: X={x}, Y={y}, Z={z}")
            resp = self.motion_api.MovJ(x, y, z, 180, 0, 180, 0)
            try:
                self._wait_for_motion(resp)
            except Exception:
                pass
            self.log_message("Moved to drop test position")
            
        except Exception as e:
            self.log_message(f"Test drop error: {str(e)}")
            messagebox.showerror("Error", f"Failed to move to drop position:\n{str(e)}")
            
    def activate_suction(self):
        """Activate suction (Digital Output)"""
        try:
            # Assuming suction is connected to DO1 (Digital Output 1)
            self.dashboard_api.DO(1, 1)  # Turn on DO1
            self.log_message("Suction activated")
            time.sleep(0.5)  # Brief delay for suction to engage
        except Exception as e:
            self.log_message(f"Suction activation error: {str(e)}")
            
    def deactivate_suction(self):
        """Deactivate suction"""
        try:
            self.dashboard_api.DO(1, 0)  # Turn off DO1
            self.log_message("Suction deactivated")
            time.sleep(0.5)  # Brief delay
        except Exception as e:
            self.log_message(f"Suction deactivation error: {str(e)}")
            
    def single_pick_and_place(self):
        """Perform a single pick and place operation"""
        if not self.connected:
            messagebox.showerror("Error", "Robot not connected!")
            return
            
        if self.operation_running:
            messagebox.showwarning("Warning", "Operation already running!")
            return
            
        # Validate positions before starting
        if not self.validate_positions():
            return
            
        # Check if stack has stickers remaining
        if self.current_stack_height <= 0:
            messagebox.showwarning("Warning", "Stack is empty! Reset stack parameters.")
            return
            
        # Run in separate thread to avoid blocking GUI
        threading.Thread(target=self._pick_and_place_sequence, daemon=True).start()
        
    def _pick_and_place_sequence(self):
        """Main pick and place sequence (runs in separate thread)"""
        self.operation_running = True
        self.emergency_stop = False
        
        try:
            # Check robot connection before starting
            if not self.check_robot_status():
                messagebox.showerror("Error", "Lost connection to robot!")
                return
                
            # Get positions
            pickup_x = float(self.pickup_x_var.get())
            pickup_y = float(self.pickup_y_var.get())
            pickup_z = self.get_adaptive_pickup_height()
            
            drop_x = float(self.drop_x_var.get())
            drop_y = float(self.drop_y_var.get())
            drop_z = float(self.drop_z_var.get())
            
            self.log_message(f"Starting pick and place sequence (Stack height: {self.current_stack_height:.1f}mm)")
            self.log_message(f"Pickup position: X={pickup_x}, Y={pickup_y}, Z={pickup_z}")
            self.log_message(f"Drop position: X={drop_x}, Y={drop_y}, Z={drop_z}")
            
            # Step 1: Move to pickup position (above stack)
            if self.emergency_stop or not self.check_robot_status():
                return
            safe_pickup_z = pickup_z + self.safety_offset
            self.log_message(f"Step 1: Moving to pickup approach: X={pickup_x}, Y={pickup_y}, Z={safe_pickup_z}")
            resp = self.motion_api.MovL(pickup_x, pickup_y, safe_pickup_z, 180, 0, 180, 0)
            try:
                self._wait_for_motion(resp, timeout=8.0)
            except Exception:
                time.sleep(1)
            
            # Step 2: Move down to sticker level
            if self.emergency_stop or not self.check_robot_status():
                return
            self.log_message(f"Step 2: Moving down to sticker level: Z={pickup_z}")
            resp = self.motion_api.MovL(pickup_x, pickup_y, pickup_z, 180, 0, 180, 0)
            try:
                self._wait_for_motion(resp, timeout=6.0)
            except Exception:
                time.sleep(0.5)
            
            # Step 3: Activate suction
            if self.emergency_stop or not self.check_robot_status():
                return
            self.log_message("Step 3: Activating suction")
            self.activate_suction()
            
            # Step 4: Move up with sticker
            if self.emergency_stop or not self.check_robot_status():
                return
            self.log_message(f"Step 4: Moving up with sticker: Z={safe_pickup_z}")
            resp = self.motion_api.MovL(pickup_x, pickup_y, safe_pickup_z, 180, 0, 180, 0)
            try:
                self._wait_for_motion(resp, timeout=8.0)
            except Exception:
                time.sleep(1)
            
            # Step 5: Move to drop approach position
            if self.emergency_stop or not self.check_robot_status():
                return
            safe_drop_z = drop_z + self.safety_offset
            self.log_message(f"Step 5: Moving to drop approach: X={drop_x}, Y={drop_y}, Z={safe_drop_z}")
            resp = self.motion_api.MovL(drop_x, drop_y, safe_drop_z, 180, 0, 180, 0)
            try:
                self._wait_for_motion(resp, timeout=8.0)
            except Exception:
                time.sleep(1)
            
            # Step 6: Move down to drop position
            if self.emergency_stop or not self.check_robot_status():
                return
            self.log_message(f"Step 6: Moving down to drop position: Z={drop_z}")
            resp = self.motion_api.MovL(drop_x, drop_y, drop_z, 180, 0, 180, 0)
            try:
                self._wait_for_motion(resp, timeout=6.0)
            except Exception:
                time.sleep(0.5)
            
            # Step 7: Deactivate suction
            if self.emergency_stop or not self.check_robot_status():
                return
            self.log_message("Step 7: Deactivating suction")
            self.deactivate_suction()
            
            # Step 8: Move up from drop position
            if self.emergency_stop or not self.check_robot_status():
                return
            self.log_message(f"Step 8: Moving up from drop position: Z={safe_drop_z}")
            resp = self.motion_api.MovL(drop_x, drop_y, safe_drop_z, 180, 0, 180, 0)
            try:
                self._wait_for_motion(resp, timeout=8.0)
            except Exception:
                time.sleep(1)
            
            # Update pickup count and stack height
            self.pickup_count += 1
            self.update_stack_height()
            
            self.log_message(f"Pick and place sequence completed successfully! (Pickup #{self.pickup_count})")
            
        except Exception as e:
            self.log_message(f"Pick and place error: {str(e)}")
            messagebox.showerror("Error", f"Pick and place failed:\n{str(e)}")
        finally:
            self.operation_running = False
            
    def start_continuous_operation(self):
        """Start continuous pick and place operation"""
        if not self.connected:
            messagebox.showerror("Error", "Robot not connected!")
            return
            
        if self.operation_running:
            # Stop continuous operation
            self.emergency_stop = True
            self.continuous_btn.config(text="Start Continuous")
            self.log_message("Stopping continuous operation...")
        else:
            # Start continuous operation
            self.continuous_btn.config(text="Stop Continuous")
            threading.Thread(target=self._continuous_operation, daemon=True).start()
            
    def _continuous_operation(self):
        """Continuous pick and place operation"""
        self.operation_running = True
        self.emergency_stop = False
        
        self.log_message("Starting continuous operation...")
        
        while not self.emergency_stop and self.current_stack_height > 0:
            try:
                self._pick_and_place_sequence()
                if not self.emergency_stop:
                    time.sleep(2)  # Brief pause between operations
            except Exception as e:
                self.log_message(f"Continuous operation error: {str(e)}")
                break
                
        self.continuous_btn.config(text="Start Continuous")
        self.operation_running = False
        
        if self.current_stack_height <= 0:
            self.log_message("Stack empty - continuous operation completed")
        else:
            self.log_message("Continuous operation stopped")
            
    def emergency_stop_operation(self):
        """Emergency stop all operations"""
        self.emergency_stop = True
        self.operation_running = False
        
        try:
            if self.connected:
                # Stop robot movement immediately
                self.dashboard_api.Stop()
                self.log_message("Robot movement stopped")
                
                # Turn off suction for safety
                self.dashboard_api.DO(1, 0)  # Emergency suction off
                self.log_message("Suction emergency shutoff")
                
                # Reset button states
                self.continuous_btn.config(text="Start Continuous")
                
            self.log_message("EMERGENCY STOP ACTIVATED")
            messagebox.showwarning("Emergency Stop", "All operations stopped!\nRobot movement halted and suction deactivated.")
            
        except Exception as e:
            self.log_message(f"Emergency stop error: {str(e)}")
            messagebox.showerror("Emergency Stop Error", f"Error during emergency stop:\n{str(e)}")
            
    def initialize_pump_control(self):
        """Initialize pump GPIO control"""
        if not GPIO_AVAILABLE:
            self.log_message("GPIO not available - pump control disabled")
            return
            
        try:
            # Initialize GPIO devices (active_low=True to match the C++ example)
            self.pump_device = DigitalOutputDevice(self.pump_pin, active_high=False)
            self.vent_device = DigitalOutputDevice(self.vent_pin, active_high=False)
            
            # Start with pump OFF
            self.pump_off()
            self.log_message("Pump control initialized")
        except Exception as e:
            self.log_message(f"Pump initialization failed: {str(e)}")
            GPIO_AVAILABLE = False
            self.pump_device = None
            self.vent_device = None
            
    def pump_on(self):
        """Turn pump ON"""
        if not GPIO_AVAILABLE or not self.pump_device:
            self.log_message("Pump control not available")
            return
            
        try:
            self.pump_device.on()  # energize main solenoid
            self.pump_state = True
            self.log_message("Pump turned ON")
        except Exception as e:
            self.log_message(f"Pump ON failed: {str(e)}")
            GPIO_AVAILABLE = False
            
    def pump_off(self):
        """Turn pump OFF with venting"""
        if not GPIO_AVAILABLE or not self.pump_device:
            self.log_message("Pump control not available")
            return
            
        try:
            # Stop main solenoid
            self.pump_device.off()
            time.sleep(0.05)  # 50ms delay
            
            # Open vent for 1 second
            if self.vent_device:
                self.vent_device.on()
                time.sleep(1)
                self.vent_device.off()
                
            time.sleep(0.05)  # 50ms delay
            self.pump_state = False
            self.log_message("Pump turned OFF (vented)")
        except Exception as e:
            self.log_message(f"Pump OFF failed: {str(e)}")
            GPIO_AVAILABLE = False
            
    def toggle_pump(self):
        """Toggle pump state"""
        if self.pump_state:
            self.pump_off()
        else:
            self.pump_on()
            
    def cleanup_pump_control(self):
        """Cleanup pump GPIO control"""
        if not GPIO_AVAILABLE:
            return
            
        try:
            # Ensure pump is safely off
            if self.pump_state:
                self.pump_off()
            else:
                # Just ensure valves are off
                if self.pump_device:
                    self.pump_device.off()
                if self.vent_device:
                    self.vent_device.off()
                    
            # Close GPIO devices
            if self.pump_device:
                self.pump_device.close()
                self.pump_device = None
            if self.vent_device:
                self.vent_device.close()
                self.vent_device = None
                
            self.log_message("Pump control cleaned up")
        except Exception as e:
            self.log_message(f"Pump cleanup failed: {str(e)}")
            GPIO_AVAILABLE = False
            
    def on_closing(self):
        """Handle application closing"""
        if self.connected:
            try:
                self.deactivate_suction()
                self.disconnect_robot()
            except:
                pass
        self.root.destroy()

def main():
    root = tk.Tk()
    app = DobotStickerPicker(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()