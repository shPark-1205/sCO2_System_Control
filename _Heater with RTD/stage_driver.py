import ctypes
import time
import os
import sys


class StepMotorController:
    def __init__(self, config):
        """
        초기화 및 DLL 로드
        :param config: NetID, Port, AxisNo, DLL Path 등을 담은 딕셔너리
        """
        self.config = config
        self.lib = None
        self.net_id = config.get('net_id', 1)  # Driver의 ipv4 주소 네 번째 자리
        self.port = config.get('port', 10025)  # UDP 통신 포트 번호 (변경 금지)
        self.axis = config.get('axis_no', 0)   # 축 번호 (0: 1번 축, 1: 2번 축)
        dll_path = config.get('dll_path', './EMotionUniDevice.dll')

        # 기본 이송 파라미터
        self.velocity = config.get('velocity', 90)  # 운송 속도 [mm/min]
        self.accel = config.get('accel', 200)       # 가속 시간 [msec]
        self.decel = config.get('decel', 200)       # 감속 시간 [msec]
        self.jerk_acc = config.get('jerk_acc', 66)  # 가속 jerk [%]
        self.jerk_dec = config.get('jerk_dec', 66)  # 감속 jerk [%]
        self.mode = config.get('mode', 0)           # 0: 절대 좌표계 이동, 1: 상대 좌표계 이동

        if not os.path.exists(dll_path):
            print(f"❌ [Error] DLL 파일 없음: {dll_path}")
            sys.exit(1)

        try:
            self.lib = ctypes.CDLL(dll_path)
            self._setup_functions()
            print(f"✅ [Motor] 라이브러리 로드 성공")
        except Exception as e:
            print(f"❌ [Motor] DLL 로드 실패: {e}")
            sys.exit(1)

    def _setup_functions(self):
        """API 함수 정의 (내부용)"""
        # --- [Environment] ---
        self.lib.eUniConnect.argtypes = [ctypes.c_int, ctypes.c_int]
        self.lib.eUniConnect.restype = ctypes.c_int
        self.lib.eUniIsConnected.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_bool)]
        self.lib.eUniIsConnected.restype = ctypes.c_int
        self.lib.eUniCheckConnection.argtypes = [ctypes.c_int]
        self.lib.eUniCheckConnection.restype = ctypes.c_int
        self.lib.eUniGetSystemErrorCode.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.lib.eUniGetSystemErrorCode.restype = ctypes.c_int
        self.lib.eUniGetAxisCount.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.lib.eUniGetAxisCount.restype = ctypes.c_int
        self.lib.eUniDisconnect.argtypes = [ctypes.c_int]
        self.lib.eUniDisconnect.restype = ctypes.c_int

        # --- [Status] ---
        self.lib.eUniGetErrorCode.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.lib.eUniGetErrorCode.restype = ctypes.c_int
        self.lib.eUniGetDriveStatus.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                                ctypes.POINTER(ctypes.c_float)]
        self.lib.eUniGetDriveStatus.restype = ctypes.c_int
        self.lib.eUniGetDriveAlarm.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.lib.eUniGetDriveAlarm.restype = ctypes.c_int
        self.lib.eUniGetMotionDone.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.lib.eUniGetMotionDone.restype = ctypes.c_int

        # --- [Signal] ---
        self.lib.eUniGetSignalStatus.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                                 ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                                                 ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                                                 ctypes.POINTER(ctypes.c_int)]
        self.lib.eUniGetSignalStatus.restype = ctypes.c_int

        # --- [Operation] ---
        self.lib.eUniSetServoOn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.lib.eUniSetServoOn.restype = ctypes.c_int
        self.lib.eUniGetServoOn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.lib.eUniGetServoOn.restype = ctypes.c_int
        self.lib.eUniReset.argtypes = [ctypes.c_int, ctypes.c_int]
        self.lib.eUniReset.restype = ctypes.c_int
        self.lib.eUniEmergencyStop.argtypes = [ctypes.c_int, ctypes.c_int]
        self.lib.eUniEmergencyStop.restype = ctypes.c_int

        # --- [Move] ---
        self.lib.eUniMoveSCurve.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.lib.eUniMoveSCurve.restype = ctypes.c_int

    # =========================================================================
    # 🔹 1. 연결 및 기본 점검
    # =========================================================================
    def connect(self):
        print(f"\n[Connect] 연결 시도 (NetID: {self.net_id})...")
        time.sleep(0.1)
        if self.lib.eUniConnect(self.net_id, self.port) == 0:
            print(f"   ✅ 연결 성공")
            return True
        else:
            print(f"   ❌ 연결 실패")
            return False

    def disconnect(self):
        print(f"\n[Disconnect] 연결 해제...")
        time.sleep(0.1)
        self.lib.eUniDisconnect(self.net_id)
        print(f"   ✅ 연결 해제 완료")

    def check_health(self):
        """연결 상태 및 시스템 에러 확인"""
        is_connected = ctypes.c_bool()

        # 리턴값은 에러코드(0=Success)
        ret = self.lib.eUniIsConnected(self.net_id, ctypes.byref(is_connected))

        # 1. 함수 호출 자체가 실패했거나 (ret != 0)
        # 2. 호출은 성공했지만 연결 상태가 False인 경우 (is_connected.value == False)
        if ret != 0 or not is_connected.value:
            print(f"   ⚠️ 연결 끊김 감지 (API Return: {ret}, Connected: {is_connected.value})")
            return False

        if self.lib.eUniCheckConnection(self.net_id) != 0:
            print("   ⚠️ 통신 상태 이상 (CheckConnection)")
            return False

        sys_err = ctypes.c_int()
        self.lib.eUniGetSystemErrorCode(self.net_id, ctypes.byref(sys_err))
        if sys_err.value != 0:
            print(f"   ⚠️ 시스템 에러 코드: {sys_err.value}")
        return True

    # =========================================================================
    # 🔹 2. 상태 모니터링
    # =========================================================================
    def print_status(self):
        """드라이버 상태, 알람, 신호 상태 출력"""
        print("\n[Status] 상태 점검...")
        time.sleep(0.5)

        # Drive Status
        status_data = (ctypes.c_int * 12)()
        fstatus_data = (ctypes.c_float * 12)()
        if self.lib.eUniGetDriveStatus(self.net_id, self.axis, status_data, fstatus_data) == 0:
            print(f"   🔎 Position: {status_data[0]/1000/10} mm")  # um to mm
            print(f"   🔎 RPM/Current : {fstatus_data[1]:.1f} rpm / {fstatus_data[2]:.2f} A")

        # Signal Status
        limitN, limitP, home, alarm, ready, on = [ctypes.c_int() for _ in range(6)]
        self.lib.eUniGetSignalStatus(self.net_id, self.axis, ctypes.byref(limitN), ctypes.byref(limitP),
                                     ctypes.byref(home), ctypes.byref(alarm), ctypes.byref(ready), ctypes.byref(on))

        print(
            f"   🔎 Signal: Limit(-/+):{limitN.value}/{limitP.value}, Home:{home.value}, Alarm:{alarm.value}, Ready:{ready.value}, ServoOn:{on.value}")
        return alarm.value  # 알람 여부 반환

    # =========================================================================
    # 🔹 3. 동작 제어
    # =========================================================================
    def reset_alarm(self):
        print("[Control] 알람 초기화 (Reset)...")
        time.sleep(0.1)
        self.lib.eUniReset(self.net_id, self.axis)
        time.sleep(1)

    def set_servo(self, enable: bool):
        time.sleep(0.1)
        val = 1 if enable else 0
        cmd = "ON" if enable else "OFF"
        print(f"[Control] Servo {cmd} 요청...")
        if self.lib.eUniSetServoOn(self.net_id, self.axis, val) != 0:
            print(f"   ❌ 명령 실패")
            return False

        time.sleep(1)  # 반영 대기

        # 확인
        on_status = ctypes.c_int()
        self.lib.eUniGetServoOn(self.net_id, self.axis, ctypes.byref(on_status))
        if on_status.value == val:
            print(f"   ✅ Servo {cmd} 완료")
            return True
        else:
            print(f"   ⚠️ Servo 상태 불일치")
            return False

    def emergency_stop(self):
        print("🛑 긴급 정지!")
        self.lib.eUniEmergencyStop(self.net_id, self.axis)

    # =========================================================================
    # 🔹 4. 이동 명령 (변수화)
    # =========================================================================
    def move_to_position(self, position_um):
        """
        특정 위치로 이동 (Absolute Move)
        :param position_um: 이동할 위치 (단위: um)
        """
        time.sleep(0.5)
        print(f"\n[Move] {position_um} um 로 이동 시작...")
        ret = self.lib.eUniMoveSCurve(
            self.net_id, self.axis,
            self.velocity, self.accel, self.decel,
            self.jerk_acc, self.jerk_dec,
            position_um, self.mode
        )
        if ret == 0:
            return True
        else:
            print(f"   ❌ 이동 명령 실패 (Code: {ret})")
            return False

    def wait_done(self, timeout=30):
        """이동 완료 대기"""
        time.sleep(0.5)  # 초기 대기 (명령 전달 시간 고려)

        done = ctypes.c_int(0)
        start = time.time()
        while time.time() - start < timeout:
            self.lib.eUniGetMotionDone(self.net_id, self.axis, ctypes.byref(done))
            if done.value == 1:
                print("   ✅ 이동 완료")
                return True
            time.sleep(0.1)

        print("   ⚠️ 이동 타임아웃")
        return False