import customtkinter as ctk
import threading
import time
from datetime import datetime, timedelta

# --- Module Imports ---
from stage_driver import StepMotorController
from RTD_logger_with_stage import DAQLogger
from heater_driver import HeaterController
from heater_logger import RealTimePowerLogger

# =================================================================================
# ⚙️ GLOBAL SETTINGS (User Input)
# =================================================================================

# 1. Experiment Metadata
EXP_DATE = "20260123"
EXP_BASE_NAME = "7.771MPa_31.3C_20000"

# Manual value for 'Hd' suffix (Not related to current limit)
HD_VALUE = "2.00"

# 2. Heater Settings
TARGET_VOLTAGE = 28.0  # ★ Set your target voltage here (V)
CURRENT_LIMIT_VAL = 2.0  # Used for Power Supply config only, not in filename

# 3. Dynamic Sub-Objective Name Generation
# Logic: int(TARGET_VOLTAGE) ensures '28.0' becomes '28'
# Example Result: "7.771MPa_31.3C_40000_28V_Hd2.00"
EXP_SUB_OBJECTIVE = f"{EXP_BASE_NAME}_{int(TARGET_VOLTAGE)}V_Hd{HD_VALUE}"

# 4. Target Positions (Unit: um)
TARGET_POSITIONS_UM = [0, 9000, 18000, 27000, 36000, 45000, 54000, 63000]

# 5. Timing Parameters (Seconds)
TIME_HEATER_LOG_INIT = 2  # Wait time after starting heater logger
TIME_HEATER_POWER_INIT = 3  # Wait time after turning on heater power
TIME_HEATER_COOLING = 8  # Wait time after turning off heater (Cooling)
TIME_BUFFER = 2  # General buffer time for communication delays
STABILIZATION_SEC = 60  # Stabilization time between movements

# =================================================================================
# ⚙️ DETAILED CONFIGURATION (Derived from Global Settings)
# =================================================================================

# 1. Motor & Stage Config
MOTOR_CONFIG = {
    "net_id": 1,
    "port": 10025,
    "axis_no": 0,
    "dll_path": "./EMotionUniDevice.dll",
    "velocity": 180,  # mm/min
    "accel": 200,  # ms
    "decel": 200,  # ms
    "jerk_acc": 66,
    "jerk_dec": 66,
    "mode": 0,  # 0: Absolute
    "stabilization_sec": STABILIZATION_SEC,
}

# 2. RTD Logger Config
RTD_CONFIG = {
    "base_exp_objective": EXP_DATE,
    "base_sub_objective": EXP_SUB_OBJECTIVE,
    "logging_duration_min": 0.5,
    "nplc": 2.0,
    "visa_resource_name": "USB0::0x05E6::0x6510::04444312::0::INSTR",
    "rtd_channels_str": "104, 113, 106, 107, 114, 109",
    "calibration_file_path": './A_calibration_curve_RTD_poly_final_Quadratic_2to7RTD_new3.xlsx',
    "normalization_enabled": True,
    "normalization_row_index": 7,
}

# 3. Heater Power Supply Config (IX_1501)
HEATER_POWER_CONFIG = {
    "visa_address": "ASRL3::INSTR",
    "current_limit_amp": CURRENT_LIMIT_VAL,  # Used here for safety
    "target_voltage": TARGET_VOLTAGE
}

# 4. Heater Data Logger Config (34970A)
HEATER_LOGGER_CONFIG = {
    "exp_objective": EXP_DATE,
    "exp_sub_objective": EXP_SUB_OBJECTIVE,  # Unified with RTD config
    "visa_resource_name": "USB0::0x2A8D::0x5101::MY58037069::0::INSTR",
    "voltage_channel": 213,
    "current_channel": 215,
    "shunt_resistance_ohm": 0.1,
    "nplc": 2,
    "trigger_interval_sec": 2
}


# =================================================================================
# 🧵 Main Experiment Thread
# =================================================================================
class ExperimentThread(threading.Thread):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.is_running = True

        # Initialize Controllers
        self.motor = StepMotorController(MOTOR_CONFIG)
        self.rtd_logger = DAQLogger(RTD_CONFIG)
        self.heater_ps = HeaterController(HEATER_POWER_CONFIG)
        self.heater_log = RealTimePowerLogger(HEATER_LOGGER_CONFIG)

    def run(self):
        self.app.log("🚀 Starting integrated experiment sequence.")

        success = False

        # --- 1. Connect All Devices ---
        if not self._connect_all():
            self._cleanup_all()
            self.app.finish_experiment(success=False)
            return

        try:
            # --- 2. Motor Initialization & Status Check ---
            self.motor.reset_alarm()
            if not self.motor.set_servo(True):
                self.app.log("❌ Servo On Failed")
                return

            # Pre-flight Check
            if not self.motor.check_health():
                self.app.log("❌ Motor Status Abnormal")
                return

            self.app.log("✅ All devices ready.")
            time.sleep(1)

            # ====================================================
            # [Step 1] Start Heater Logging
            # ====================================================
            self.app.log("📈 [Heater] Starting data logging...")
            self.heater_log.start_logging()
            time.sleep(TIME_HEATER_LOG_INIT)

            # ====================================================
            # [Step 2] Apply Heater Power
            # ====================================================
            target_v = HEATER_POWER_CONFIG['target_voltage']
            self.app.log(f"🔥 [Heater] Applying Power ({target_v} V)...")
            self.heater_ps.set_voltage(target_v)
            self.heater_ps.output_on()
            time.sleep(TIME_HEATER_POWER_INIT)

            # ====================================================
            # [Step 3] Stage Movement & RTD Measurement Loop
            # ====================================================
            total_steps = len(TARGET_POSITIONS_UM)
            wait_sec = MOTOR_CONFIG['stabilization_sec']

            for idx, pos_um in enumerate(TARGET_POSITIONS_UM):
                if not self.is_running: break

                pos_mm = pos_um / 1000
                self.app.highlight_current_row(idx)

                # (A) Movement Phase
                self.app.update_sub_status(idx, "move", "running")
                self.app.log(f"🔹 Step {idx + 1}: 🚜 Moving to {pos_mm}mm...")

                if self.motor.move_to_position(pos_um):
                    if not self.motor.wait_done(timeout=60):
                        self.app.log("⚠️ Movement Timeout!")
                        break
                else:
                    self.app.log("❌ Movement Command Failed!")
                    break

                self.app.update_sub_status(idx, "move", "done")
                if not self.is_running: break

                # (B) Stabilization Phase
                self.app.update_sub_status(idx, "wait", "running")
                self.app.log(f"⏳ Stabilizing ({wait_sec} sec)...")

                for _ in range(wait_sec):
                    if not self.is_running: break
                    time.sleep(1)

                self.app.update_sub_status(idx, "wait", "done")
                if not self.is_running: break

                # (C) RTD Measurement Phase
                self.app.update_sub_status(idx, "measure", "running")
                tag_name = f"-r{int(pos_mm):02d}mm"
                self.app.log(f"📸 RTD Measurement Started ({RTD_CONFIG['logging_duration_min']} min)...")

                if self.rtd_logger.connect():
                    self.rtd_logger.execute_logging(tag_name)
                    self.rtd_logger.disconnect()
                else:
                    self.app.log("❌ RTD Logger Connection Failed (Skipped)")

                self.app.update_sub_status(idx, "measure", "done")

                # Update Progress Bar
                self.app.update_progress((idx + 1) / total_steps)

            # ====================================================
            # [Step 4] Turn Off Heater Power
            # ====================================================
            self.app.log("❄️ [Heater] Turning Power OFF...")
            self.heater_ps.output_off()

            # ====================================================
            # [Step 5] Stop Logging after Cooling
            # ====================================================
            self.app.log(f"⏳ Waiting for cooling & logging termination ({TIME_HEATER_COOLING} sec)...")
            time.sleep(TIME_HEATER_COOLING)

            self.app.log("⏹️ [Heater] Stopping logging & Saving data...")
            self.heater_log.stop_logging()

            # ====================================================
            # [Step 6] Return to Home
            # ====================================================
            if self.is_running:
                self.app.set_info("All steps complete. Returning to Home...", "orange")
                self.app.log("🚜 Returning to Home (0mm)...")

                if self.motor.check_health():
                    self.motor.reset_alarm()
                    self.motor.set_servo(True)
                    if self.motor.move_to_position(0):
                        self.motor.wait_done(timeout=60)
                success = True

        except Exception as e:
            self.app.log(f"❌ Critical Error: {e}")
            import traceback
            traceback.print_exc()

        finally:
            self._cleanup_all()
            self.app.finish_experiment(success=success and self.is_running)

    def _connect_all(self):
        """Attempt to connect all devices."""
        if not self.motor.connect():
            self.app.log("❌ Motor Connection Failed")
            return False
        if not self.heater_ps.connect():
            self.app.log("❌ Heater Power Supply Connection Failed")
            return False
        if not self.heater_log.connect():
            self.app.log("❌ Heater Logger Connection Failed")
            return False
        return True

    def _cleanup_all(self):
        """Safe shutdown sequence."""
        self.app.log("🧹 Executing cleanup sequence...")

        if self.heater_ps:
            try:
                self.heater_ps.output_off()
                self.heater_ps.disconnect()
            except:
                pass

        if self.heater_log:
            try:
                self.heater_log.stop_logging()
                self.heater_log.disconnect()
            except:
                pass

        if self.motor:
            try:
                self.motor.set_servo(False)
                self.motor.disconnect()
            except:
                pass

        if self.rtd_logger:
            try:
                self.rtd_logger.disconnect()
            except:
                pass

    def stop(self):
        """Emergency Stop"""
        self.is_running = False
        self.app.log("🛑 Emergency Stop triggered! Shutting down safely.")

        if self.motor: self.motor.emergency_stop()
        if self.heater_ps: self.heater_ps.output_off()


# =================================================================================
# 🖥️ GUI Application
# =================================================================================
class ExperimentApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Integrated Automation System")
        self.geometry("900x900")
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.thread = None
        self.rows_ui = []
        self._main_thread_id = threading.get_ident()

        self._setup_ui()

    def _on_ui_thread(self):
        return threading.get_ident() == self._main_thread_id

    def _ui_call(self, callback, *args, **kwargs):
        if self._on_ui_thread():
            callback(*args, **kwargs)
        else:
            self.after(0, lambda: callback(*args, **kwargs))

    def _setup_ui(self):
        # 1. Header
        self.header_frame = ctk.CTkFrame(self)
        self.header_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(self.header_frame, text="Automated Measurement System", font=("Arial", 22, "bold")).pack(pady=5)
        self.info_label = ctk.CTkLabel(self.header_frame, text="Ready to Start", font=("Arial", 16), text_color="gray")
        self.info_label.pack(pady=2)
        self.time_label = ctk.CTkLabel(self.header_frame, text="Start: --:-- | Duration: -- min | ETA: --:--",
                                       font=("Arial", 14))
        self.time_label.pack(pady=5)

        # 2. Progress Bar
        self.progress_bar = ctk.CTkProgressBar(self, height=15)
        self.progress_bar.pack(fill="x", padx=20, pady=5)
        self.progress_bar.set(0)

        # 3. Scroll Frame
        self.scroll_frame = ctk.CTkScrollableFrame(self, label_text="Experiment Steps Detail")
        self.scroll_frame.pack(fill="both", expand=True, padx=20, pady=10)

        # Header Row
        header_row = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        header_row.pack(fill="x", pady=2)
        ctk.CTkLabel(header_row, text="Step Info", width=150, anchor="w", font=("Arial", 12, "bold")).pack(side="left",
                                                                                                           padx=10)
        ctk.CTkLabel(header_row, text="Movement", width=120, font=("Arial", 12, "bold")).pack(side="left", padx=5)
        ctk.CTkLabel(header_row, text="Stabilization", width=120, font=("Arial", 12, "bold")).pack(side="left", padx=5)
        ctk.CTkLabel(header_row, text="Measurement", width=120, font=("Arial", 12, "bold")).pack(side="left", padx=5)

        # Step Rows
        for i, pos in enumerate(TARGET_POSITIONS_UM):
            mm = pos / 1000
            row_frame = ctk.CTkFrame(self.scroll_frame)
            row_frame.pack(fill="x", pady=3)

            lbl_step = ctk.CTkLabel(row_frame, text=f"Step {i + 1} : {mm}mm", width=150, anchor="w", font=("Arial", 13))
            lbl_step.pack(side="left", padx=10)

            lbl_move = self._create_status_label(row_frame, "🚜 Move")
            lbl_wait = self._create_status_label(row_frame, "⏳ Wait")
            lbl_meas = self._create_status_label(row_frame, "📸 Measure")

            self.rows_ui.append(
                {"frame": row_frame, "step_lbl": lbl_step, "move": lbl_move, "wait": lbl_wait, "measure": lbl_meas})

        # 4. Log Box
        self.log_box = ctk.CTkTextbox(self, height=120, font=("Consolas", 12))
        self.log_box.pack(fill="x", padx=20, pady=10)
        self.log_box.insert("0.0", "System Initialized.\n")

        # 5. Buttons
        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.pack(fill="x", padx=20, pady=20)
        self.start_btn = ctk.CTkButton(self.btn_frame, text="START EXPERIMENT", command=self.start_experiment,
                                       height=50, fg_color="green", hover_color="darkgreen", font=("Arial", 14, "bold"))
        self.start_btn.pack(side="left", fill="x", expand=True, padx=5)
        self.stop_btn = ctk.CTkButton(self.btn_frame, text="EMERGENCY STOP", command=self.emergency_stop, height=50,
                                      fg_color="red", hover_color="darkred", font=("Arial", 14, "bold"),
                                      state="disabled")
        self.stop_btn.pack(side="right", fill="x", expand=True, padx=5)

    def _create_status_label(self, parent, text):
        lbl = ctk.CTkLabel(parent, text=f"⬜ {text}", width=120, text_color="gray")
        lbl.pack(side="left", padx=5)
        return lbl

    def start_experiment(self):
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress_bar.set(0)
        self.info_label.configure(text="Initializing...", text_color="white")

        # -----------------------------------------------------------------
        # Time Estimation Logic
        # -----------------------------------------------------------------
        start_time = datetime.now()

        velo_mm_min = MOTOR_CONFIG['velocity']
        velo_mm_sec = velo_mm_min / 60.0
        stab_sec = MOTOR_CONFIG['stabilization_sec']
        meas_sec = RTD_CONFIG['logging_duration_min'] * 60

        # Initial Delays
        total_sec = TIME_HEATER_LOG_INIT + TIME_HEATER_POWER_INIT

        current_pos_um = 0

        # Loop Estimation
        for target_pos_um in TARGET_POSITIONS_UM:
            dist_mm = abs(target_pos_um - current_pos_um) / 1000.0
            move_sec = (dist_mm / velo_mm_sec) + TIME_BUFFER
            total_sec += (move_sec + stab_sec + meas_sec)
            current_pos_um = target_pos_um

        # End Process Estimation (Return + Heater Cooling + Buffer)
        return_dist_mm = abs(current_pos_um - 0) / 1000.0
        return_sec = (return_dist_mm / velo_mm_sec) + TIME_BUFFER
        total_sec += (return_sec + TIME_HEATER_COOLING + 5)

        end_time = start_time + timedelta(seconds=total_sec)
        total_min = total_sec / 60.0

        self.time_label.configure(
            text=f"Start: {start_time.strftime('%H:%M:%S')} | Duration: ~{total_min:.1f} min | ETA: {end_time.strftime('%H:%M:%S')}")

        # Reset UI
        for ui in self.rows_ui:
            ui['frame'].configure(fg_color=["gray90", "gray20"])
            self._reset_label(ui['move'], "🚜 Move")
            self._reset_label(ui['wait'], "⏳ Wait")
            self._reset_label(ui['measure'], "📸 Measure")

        # Start Thread
        self.thread = ExperimentThread(self)
        self.thread.start()

    def _reset_label(self, label, text):
        label.configure(text=f"⬜ {text}", text_color="gray", font=("Arial", 13))

    def emergency_stop(self):
        if self.thread and self.thread.is_alive():
            self.log("🛑 Emergency Stop Triggered!")
            self.thread.stop()
            self.stop_btn.configure(state="disabled")

    def highlight_current_row(self, index):
        if not self._on_ui_thread():
            self._ui_call(self.highlight_current_row, index)
            return

        for i, ui in enumerate(self.rows_ui):
            if i == index:
                ui['frame'].configure(fg_color=["gray85", "gray30"])
            else:
                ui['frame'].configure(fg_color=["gray90", "gray20"])

    def update_sub_status(self, index, stage, state):
        if not self._on_ui_thread():
            self._ui_call(self.update_sub_status, index, stage, state)
            return

        ui = self.rows_ui[index]
        target_lbl = ui[stage]
        base_text = {"move": "🚜 Move", "wait": "⏳ Wait", "measure": "📸 Measure"}[stage]

        if state == "running":
            target_lbl.configure(text=f"🟡 {base_text}", text_color="yellow", font=("Arial", 13, "bold"))
            self.info_label.configure(text=f"Step {index + 1}: {base_text}ing...", text_color="yellow")
        elif state == "done":
            target_lbl.configure(text=f"✅ {base_text}", text_color="#00FF00", font=("Arial", 13))

    def update_progress(self, value):
        self._ui_call(self.progress_bar.set, value)

    def log(self, message):
        if not self._on_ui_thread():
            self._ui_call(self.log, message)
            return

        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.insert("end", f"[{timestamp}] {message}\n")
        self.log_box.see("end")

    def set_info(self, text, color="white"):
        self._ui_call(self.info_label.configure, text=text, text_color=color)

    def finish_experiment(self, success):
        if not self._on_ui_thread():
            self._ui_call(self.finish_experiment, success)
            return

        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        msg = "Experiment Complete" if success else "Experiment Interrupted"
        color = "white" if success else "red"
        self.info_label.configure(text=msg, text_color=color)
        self.log(f"=== {msg} ===")


if __name__ == "__main__":
    app = ExperimentApp()
    app.mainloop()
