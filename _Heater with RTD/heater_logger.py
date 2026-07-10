import time
import datetime
import pyvisa
import numpy as np
import pandas as pd
import threading
import os
from typing import Dict, Any, List


class RealTimePowerLogger:
    """
    Measures voltage and current from an Agilent 34970A DMM in real-time (Background Mode).
    GUI/Plotting features are removed for integration.
    """

    def __init__(self, config: Dict[str, Any]):
        """Initializes the logger."""
        self.config = config
        self.is_running = False
        self.run_count = 0
        self.start_time = 0.0

        self.rm = None
        self.dmm = None
        self.thread = None

        # Data storage
        self.results_data: List[Dict[str, Any]] = []

    def connect(self):
        """Connects to the DMM and configures it."""
        print("🔄 [HeaterLogger] Connecting to DMM...")
        try:
            self.rm = pyvisa.ResourceManager("visa64.dll")
            self.dmm = self.rm.open_resource(self.config['visa_resource_name'])
            self.dmm.write_termination = "\n"
            self.dmm.timeout = 30000

            # IDN check
            idn = self.dmm.query('*IDN?').strip()
            print(f"✅ [HeaterLogger] Device connected: {idn}")

            # Configure
            self._configure_dmm()

            # Initialize measurement
            self.dmm.write("INIT")
            time.sleep(0.5)

            return True

        except Exception as e:
            print(f"❌ [HeaterLogger] Connection failed: {e}")
            return False

    def _configure_dmm(self):
        """Configures the DMM using the original command sequence."""
        scan_channels = f"(@{self.config['voltage_channel']},{self.config['current_channel']})"
        nplc = self.config['nplc']

        commands = [
            "*RST", "*CLS", "DISP OFF",  # Display OFF for speed
            f"CONF:VOLT:DC DEF, {scan_channels}",
            f"SENS:VOLT:DC:RANG:AUTO ON, {scan_channels}",
            f"SENS:VOLT:DC:NPLC {nplc}, {scan_channels}",
            f"INP:IMP:AUTO ON, {scan_channels}",
            f"ROUT:CHAN:DEL 0.001, {scan_channels}",
            f"ROUT:CHAN:DEL:AUTO ON, {scan_channels}",
            f"ROUT:SCAN {scan_channels}",
            "TRIG:SOUR BUS",  # Software trigger (*TRG)
            "TRIG:COUN INF",  # Continuous trigger
            "FORM:READ:CHAN ON", "FORM:READ:TIME ON",
            "FORM:READ:TIME:TYPE REL",
        ]

        print("⚙️ [HeaterLogger] Applying settings...")
        for cmd in commands:
            self.dmm.write(cmd)
            time.sleep(0.05)
        print("✅ [HeaterLogger] Configuration complete.")

    def start_logging(self):
        """Starts the logging loop in a background thread."""
        if self.is_running:
            print("⚠️ [HeaterLogger] Already running.")
            return

        self.is_running = True
        self.run_count = 0
        self.start_time = time.time()
        self.results_data = []  # Reset data

        # Start background thread
        self.thread = threading.Thread(target=self._logging_loop, daemon=True)
        self.thread.start()
        print("▶️ [HeaterLogger] Background logging started.")

    def stop_logging(self):
        """Stops the logging loop and saves data."""
        if not self.is_running:
            return

        print("⏹️ [HeaterLogger] Stopping logging...")
        self.is_running = False

        if self.thread:
            self.thread.join(timeout=5.0)  # Wait for thread to finish

        self._save_data_to_excel()

    def _logging_loop(self):
        """Main loop for data acquisition (Threaded)."""
        trigger_interval = self.config.get('trigger_interval_sec', 2)

        # Initial Trigger
        try:
            self.dmm.write("*TRG")
            time.sleep(trigger_interval)
        except:
            pass

        while self.is_running:
            try:
                loop_start = time.time()

                # 1. Get Data
                self._get_and_log_data()

                # 2. Next Trigger
                self.dmm.write("*TRG")

                # 3. Wait for interval
                elapsed = time.time() - loop_start
                sleep_time = trigger_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

            except Exception as e:
                print(f"⚠️ [HeaterLogger] Loop error: {e}")
                time.sleep(1)

    def _get_and_log_data(self):
        """Encapsulates data retrieval and parsing logic."""
        self.run_count += 1
        current_time_float = time.time()

        try:
            self.dmm.write("R?")
            raw_data_str = self.dmm.read()
            processed_data = self._parse_agilent_data(raw_data_str)

            if not processed_data:
                # print(f"⚠️ [HeaterLogger] No data parsed on run #{self.run_count}")
                return

            # --- Original Data Logic ---
            voltage = processed_data.get(self.config['voltage_channel'], np.nan)
            current_voltage = processed_data.get(self.config['current_channel'], np.nan)
            current = current_voltage / self.config['shunt_resistance_ohm']

            elapsed_sec = current_time_float - self.start_time

            log_entry = {
                'Run': self.run_count,
                'Time': datetime.datetime.fromtimestamp(current_time_float).strftime('%H:%M:%S'),
                'sec': elapsed_sec,
                'V [V]': voltage,
                'C [A]': current,
                'C [V]': current_voltage
            }
            # ---------------------------

            self.results_data.append(log_entry)

            # Print status every 5 runs to avoid clutter
            # if self.run_count % 5 == 0:
            #     print(f"[HeaterLog] {log_entry['Time']} | {voltage:.3f}V | {current:.3f}A")

        except Exception as e:
            # print(f"⚠️ [HeaterLogger] Read error: {e}")
            pass

    def _parse_agilent_data(self, raw_data_str: str) -> Dict[int, float]:
        """Parses the raw string from the Agilent DMM."""
        try:
            items = raw_data_str.strip().split(',')
            if not items or len(items) < 3: return {}

            first_item = items[0]
            start_pos = -1
            plus_pos, minus_pos = first_item.find('+'), first_item.find('-')
            if plus_pos != -1 and minus_pos != -1:
                start_pos = min(plus_pos, minus_pos)
            elif plus_pos != -1:
                start_pos = plus_pos
            elif minus_pos != -1:
                start_pos = minus_pos
            if start_pos != -1: items[0] = first_item[start_pos:]

            values = np.array(items, dtype=np.float64).reshape(-1, 3)
            result = {int(ch): np.mean(values[values[:, 2] == ch, 0]) for ch in np.unique(values[:, 2])}
            return result
        except (ValueError, IndexError):
            return {}

    def _save_data_to_excel(self):
        """Saves data to Excel."""
        if not self.results_data:
            print("ℹ️ [HeaterLogger] No data to save.")
            return

        try:
            # Setup directory
            time_title = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            base_path = f"data/_HX-{self.config['exp_objective']}"
            os.makedirs(base_path, exist_ok=True)

            filename = f"{base_path}/_HX-{self.config['exp_sub_objective']}_{time_title}.xlsx"

            # Create DataFrame
            df = pd.DataFrame(self.results_data)
            final_columns = ['Time', 'Run', 'sec', 'V [V]', 'C [A]', 'C [V]']
            df = df.reindex(columns=final_columns)

            df.to_excel(filename, index=False)
            print(f"💾 [HeaterLogger] Data saved: {filename}")

        except Exception as e:
            print(f"❌ [HeaterLogger] Save failed: {e}")

    def disconnect(self):
        """Safe cleanup."""
        self.stop_logging()

        print("🧹 [HeaterLogger] Cleaning up...")
        if self.dmm:
            try:
                self.dmm.write("ABOR")
                self.dmm.close()
            except:
                pass
        if self.rm:
            try:
                self.rm.close()
            except:
                pass
        print("✅ [HeaterLogger] Disconnected.")