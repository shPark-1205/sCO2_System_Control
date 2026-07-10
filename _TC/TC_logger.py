import os
import time
import datetime
import pyvisa
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.lines import Line2D


try:
    import wres
except ImportError:
    class DummyContextManager:
        def __enter__(self): pass

        def __exit__(self, exc_type, exc_val, exc_tb): pass


    wres = type('wres', (), {'set_resolution': lambda self, x: DummyContextManager()})()
    print("Warning: 'wres' module not found. Using a dummy context manager.")

# =================================================================================
# ⚙️ Experiment Configuration
# =================================================================================
CONFIG = {
    # --- Experiment Info ---
    "exp_objective": "20250912",
    "exp_sub_objective": "Test",

    # --- Instrument & Channel Settings ---
    "visa_resource_name": "USB0::0x2A8D::0x5101::MY58037062::0::INSTR",
    "rtd_channels_str": "301, 302, 303, 304, 305",

    # --- Measurement Settings ---
    "nplc": 2,
    "trigger_interval_sec": 1.0,

    # --- Plot Settings ---
    "plot_time_window_min": 10,
    "plot_temp_min": 21,
    "plot_temp_max": 27,
    "save_image_interval": 30,

    # --- Voltage to Temperature Conversion Coefficients ---
    "temp_conversion_coeffs": [0.0, 2.592800E-2, -7.602961E-7, 4.637791E-11, -2.165394E-15, 6.048144E-20, -7.293422E-25]
}


# =================================================================================

class DMM_Logger:
    def __init__(self, config):
        """
        Initializes the DMM logger, configures settings, and runs the entire
        process from instrument connection to data logging and shutdown.
        """
        self.config = config
        self.channels = self.config['rtd_channels_str'].split(', ')
        self.num_sensors = len(self.channels)

        self.time_title = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        self.make_dir()
        self.dmm_connect()
        self.dmm_check()

        self.dmm_setting()
        self.data_setting()
        self.plt_setting()

        # --- Original Triggering and Animation Sequence ---
        self.dmm_trg_ready()

        self.dmm.write("*TRG")
        time.sleep(self.config['trigger_interval_sec'])

        self.ani = FuncAnimation(
            fig=self.fig,
            func=self.plt_update,
            interval=self.config['trigger_interval_sec'] * 1000,
            cache_frame_data=False
        )
        plt.show()

        self.save_excel()
        self.dmm_close()

    def make_dir(self):
        """
        Creates the necessary directories for storing data files and plot images
        based on the experiment objective and a timestamp.
        """
        self.path_title = f"data/_TC-{self.config['exp_objective']}"
        self.path_sub_title = f"{self.path_title}/_vTC-{self.config['exp_sub_objective']}"
        self.output_path = f"{self.path_sub_title}/_vTC-{self.config['exp_sub_objective']}_{self.time_title}"
        try:
            os.makedirs(f"{self.output_path}/pic", exist_ok=True)
            print(f"✅ Data storage path created: {self.output_path}")
        except OSError:
            print("Error: Creating directory")

    def dmm_connect(self):
        """
        Establishes a VISA connection to the Digital Multimeter (DMM)
        using the resource name defined in the configuration.
        """
        self.rm = pyvisa.ResourceManager()
        self.dmm = self.rm.open_resource(self.config['visa_resource_name'])
        self.dmm.write_termination = "\n"
        self.dmm.timeout = 30000

    def dmm_check(self):
        """
        Verifies the DMM connection by querying its identity (*IDN?) and
        checks for any pre-existing system errors.
        """
        try:
            print(f"✅ Device connected successfully: {self.dmm.query('*IDN?').strip()}")
            print(f"✅ Device status check: {self.dmm.query('SYST:ERR?').strip()}")
            self.dmm.write("*CLS")
        except pyvisa.errors.VisaIOError as err:
            print(f"ERROR in dmm_check \n{str(err)}")

    def dmm_setting(self):
        """
        Sends a series of SCPI commands to configure the DMM for the specific
        measurement task (e.g., DC Voltage, NPLC, scan list, trigger source).
        """
        try:
            settings = [
                "*RST", "*CLS", "DISP ON",
                f"CONF:VOLT:DC DEF ,(@{self.config['rtd_channels_str']})",
                f"SENS:VOLT:DC:RANG 0.1,(@{self.config['rtd_channels_str']})",
                f"SENS:VOLT:DC:NPLC {self.config['nplc']} ,(@{self.config['rtd_channels_str']})",
                f"INP:IMP:AUTO ON ,(@{self.config['rtd_channels_str']})",
                f"ROUT:CHAN:DEL:AUTO ON ,(@{self.config['rtd_channels_str']})",
                f"ROUT:SCAN (@{self.config['rtd_channels_str']})",
                "TRIG:SOUR BUS", "TRIG:COUN INF",
                "FORM:READ:CHAN ON", "FORM:READ:TIME ON", "FORM:READ:UNIT OFF", "FORM:READ:TIME:TYPE REL"
            ]
            print("⚙️  Applying DMM settings...")
            for cmd in settings:
                self.dmm.write(cmd)
                time.sleep(0.05)
            print("✅ DMM configuration complete.")
        except pyvisa.errors.VisaIOError as err:
            print(f"ERROR in dmm_setting \n{str(err)}")

    def dmm_trg_ready(self):
        """
        Puts the DMM into a 'wait-for-trigger' state, making it ready
        to start measurements upon receiving a trigger command.
        """
        print("▶️  Initializing for measurement...")
        self.dmm.write("INIT")
        time.sleep(0.1)

    def data_setting(self):
        """
        Initializes or resets data storage containers (NumPy arrays)
        before the measurement loop begins.
        """
        self._count = 1
        self.post_COUNT = np.array([])
        self.post_TIME = np.array([])
        self.post_SEC = np.array([])
        for i in range(1, self.num_sensors + 1):
            setattr(self, f'post_TC{i}V', np.array([]))
            setattr(self, f'post_TC{i}T', np.array([]))

    def plt_setting(self):
        """
        Sets up the Matplotlib figure and axes for real-time plotting,
        including labels, lines, legends, and text objects.
        """
        self.fig, self.ax = plt.subplots(1, 1, figsize=(12, 6))
        self.fig.canvas.manager.set_window_title('Real-time Temperature Monitor')
        self.ax.set_ylabel('Temperature [°C]')
        self.ax.set_xlabel('Elapsed Time [min]')

        colors = plt.cm.tab10(np.linspace(0, 1, self.num_sensors))
        self.TC_lines = []
        for i, channel in enumerate(self.channels):
            line = Line2D([], [], color=colors[i], linewidth=2, label=f'TC {channel}')
            self.TC_lines.append(line)
            self.ax.add_line(line)

        self.ax.legend(loc='lower left')
        self.ax.set_xlim(0, self.config['plot_time_window_min'])
        self.ax.grid(True, linestyle='--')
        plt.tight_layout(pad=2.0)

        self.plot_texts = []
        for i in range(self.num_sensors):
            text = self.ax.text(
                0.05 + (i * 0.18), 0.95, "",
                transform=self.ax.transAxes, verticalalignment='top', fontsize=10, color=colors[i]
            )
            self.plot_texts.append(text)

    def plt_update(self, frame):
        """
        The main callback function for FuncAnimation. It is executed at each
        interval to get new data, update plot lines and text, and trigger
        the next DMM measurement.
        """
        # This is the original, working data acquisition and plot update logic
        self.data_gettering()

        for i in range(self.num_sensors):
            # Update plot line data
            self.TC_lines[i].set_data(
                (self.post_COUNT * self.config['trigger_interval_sec']) / 60,
                getattr(self, f'post_TC{i + 1}T')
            )
            # Update text data
            if getattr(self, f'post_TC{i + 1}T').size > 0:
                last_temp = getattr(self, f'post_TC{i + 1}T')[-1]
                self.plot_texts[i].set_text(f"TC{self.channels[i]}: {last_temp:.2f} °C")

        if self.post_SEC.size > 0:
            max_time_minute = self.post_SEC[-1] / 60
            self.ax.set_xlim(0, max_time_minute + 2)
            all_current_temps = np.concatenate(
                [getattr(self, f'post_TC{i + 1}T') for i in range(self.num_sensors) if getattr(self, f'post_TC{i + 1}T').size > 0]
            )
            if all_current_temps.size > 0:
                min_temp = np.min(all_current_temps)
                max_temp = np.max(all_current_temps)
                delta_temp = max_temp - min_temp
                self.ax.set_ylim(min_temp - delta_temp, max_temp + delta_temp)

        if self._count % self.config['save_image_interval'] == 1:
            self.save_plot_image()

        # This trigger command is essential for the next loop iteration
        self.dmm.write("*TRG")
        return self.TC_lines

    def data_gettering(self):
        """
        Requests a new measurement from the DMM ('R?') and reads the raw
        data string, then passes it to the processing function.
        """
        start_time = time.time()
        self.dmm.write("R?")
        raw_data = self.dmm.read()

        processed_data = self.data_pre_processing_Agilent(raw_data)
        if processed_data is not None:
            self.log_to_console(self._count, start_time, processed_data)
        self._count += 1

    def data_pre_processing_Agilent(self, imported_data):
        """
        Parses the raw, comma-separated string from the DMM. It cleans the
        string, converts it to a NumPy array, and calculates the mean
        value for each scanned channel.
        """
        try:
            proc_pre = imported_data.strip().split(",")
            if not proc_pre or not proc_pre[0]: return None

            first_item = proc_pre[0]
            start_pos = -1
            plus_pos = first_item.find('+')
            minus_pos = first_item.find('-')
            if plus_pos != -1: start_pos = plus_pos
            if minus_pos != -1: start_pos = minus_pos if start_pos == -1 else min(start_pos, minus_pos)

            if start_pos != -1: proc_pre[0] = first_item[start_pos:]

            proc_np = np.array(proc_pre, dtype=np.float64).reshape(-1, 3)
            proc_channel = np.unique(proc_np[:, 2])

            proc_post = np.array([])
            for i in proc_channel:
                tempo_sort = proc_np[proc_np[:, 2] == i, 0]
                tempo_mean = np.mean(tempo_sort)
                proc_post = np.append(proc_post, tempo_mean)
            return proc_post
        except (ValueError, IndexError):
            print("Warning: Could not parse data from DMM. Skipping this interval.")
            return None

    def voltage_to_temperature(self, voltage_v):
        """
        Converts a raw voltage measurement (in Volts) into a temperature
        value (in Celsius) using a polynomial equation defined in the config.
        """
        C = self.config['temp_conversion_coeffs']
        v_uv = voltage_v * 1E6
        temp = sum(c * (v_uv ** i) for i, c in enumerate(C))
        return temp

    def log_to_console(self, count, start_time_float, processed_data):
        """
        Appends the latest processed data to the main storage arrays and
        prints a formatted summary of the measurement to the console.
        """
        for i in range(self.num_sensors):
            TC_V_mv = processed_data[i] * 1000
            TC_T = self.voltage_to_temperature(processed_data[i])
            setattr(self, f'post_TC{i + 1}V', np.append(getattr(self, f'post_TC{i + 1}V'), TC_V_mv))
            setattr(self, f'post_TC{i + 1}T', np.append(getattr(self, f'post_TC{i + 1}T'), TC_T))

        self.post_COUNT = np.append(self.post_COUNT, count)
        time_str = datetime.datetime.fromtimestamp(start_time_float).strftime('%H:%M:%S')
        self.post_TIME = np.append(self.post_TIME, time_str)
        elapsed_sec = self.config['trigger_interval_sec'] * (count - 1)
        self.post_SEC = np.append(self.post_SEC, elapsed_sec)

        temp_str = "\t".join([f"{getattr(self, f'post_TC{i + 1}T')[-1]:>8.3f}" for i in range(self.num_sensors)])
        print(f"{count:0>4d}\t{time_str}\t{elapsed_sec:6.1f}\t{temp_str}")

    def save_plot_image(self):
        """
        Saves the current state of the Matplotlib plot as a PNG image file
        with a timestamp in its name.
        """
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{self.output_path}/pic/plot_{timestamp}.png"
        try:
            self.fig.savefig(filename, dpi=150)
        except Exception as e:
            print(f"Error saving image: {e}")

    def save_excel(self):
        """
        Compiles all collected data into a Pandas DataFrame and saves it
        as an Excel (.xlsx) file when the program finishes.
        """
        if self.post_COUNT.size == 0:
            print("ℹ️  No data to save.")
            return

        df = pd.DataFrame({
            'Run': self.post_COUNT,
            'Time': self.post_TIME,
            'sec': self.post_SEC
        })
        for i in range(1, self.num_sensors + 1):
            df[f'TC{self.channels[i - 1]} [mV]'] = getattr(self, f'post_TC{i}V')
        for i in range(1, self.num_sensors + 1):
            df[f'TC{self.channels[i - 1]} [°C]'] = getattr(self, f'post_TC{i}T')

        filename = f"{self.output_path}/_vTC-{self.config['exp_sub_objective']}_{self.time_title}.xlsx"
        df.to_excel(filename, index=False)
        print(f"\n💾 Data saved successfully: {filename}")

    def dmm_close(self):
        """
        Performs safe shutdown procedures by sending an ABORt command to
        the DMM and closing the VISA connection.
        """
        print("🧹 Performing cleanup and shutdown procedures.")
        try:
            self.dmm.write("ABOR")
            print(f"✅ Device status check: {self.dmm.query('SYST:ERR?').strip()}")
            self.dmm.close()
            self.rm.close()
            print("✅ DMM connection closed safely.")
        except pyvisa.errors.VisaIOError as e:
            print(f"⚠️ Error during DMM cleanup: {e}")
        print("👋 Program finished.")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("🚀 Starting Temperature Logger. Close the plot window to end.")
    print("=" * 70 + "\n")
    with wres.set_resolution(5000):
        DMM_Logger(CONFIG)