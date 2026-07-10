import pyvisa
import time


class HeaterController:
    def __init__(self, config):
        """
        히터 파워 서플라이 제어 클래스 (IX_1501)
        기존 RTDCalibrationExperiment 코드 기반
        """
        self.config = config
        self.visa_addr = config.get('visa_address', 'ASRL3::INSTR')
        # 기본값은 기존 코드의 2.0A를 따름
        self.current_limit = config.get('current_limit_amp', 2.0)
        self.rm = None
        self.inst = None

    def connect(self):
        """장비 연결 및 초기화"""
        print(f"\n[Heater] 연결 시도 ({self.visa_addr})...")
        try:
            self.rm = pyvisa.ResourceManager("visa64.dll")
            self.inst = self.rm.open_resource(self.visa_addr)

            # 기존 코드 설정 따름
            self.inst.write_termination = "\n"
            self.inst.timeout = 60000

            # 시리얼 통신 안정화 대기
            time.sleep(1)

            # 연결 확인
            try:
                idn = self.inst.query("*IDN?")
                print(f"   ✅ 히터 연결 성공: {idn.strip()}")
            except:
                print("   ⚠️ IDN 쿼리 실패 (연결은 되었을 수 있음)")

            # 초기화 실행
            self._initialize_device()
            return True

        except Exception as e:
            print(f"   ❌ 히터 연결 실패: {e}")
            return False

    def _initialize_device(self):
        """
        [기존 코드 유지] _configure_instruments의 psu_cmds 적용
        """
        print("   ⚙️ 히터 초기 설정 중...")
        # 제공해주신 레퍼런스 코드의 명령어를 그대로 사용
        psu_cmds = [
            "*RST",
            "*CLS",
            "SOUR:MODE DC",
            f"SOUR:CURR {self.current_limit}",
            "SOUR:VOLT:ALC ON",
            "SOUR:VOLT:SENS:SOUR EXT"
        ]

        for cmd in psu_cmds:
            self.inst.write(cmd)
            # 루프 내 sleep 언급은 없었으나, 안전을 위해 최소한의 딜레이 권장
            # 원본 코드에서는 루프 후 print만 있었음.
            # 시리얼 통신 특성상 명령 간 약간의 딜레이는 안전함.
            time.sleep(0.5)

        print("   ✅ 히터 설정 완료")

    def set_voltage(self, voltage):
        """전압 설정 (VOLT <val>)"""
        try:
            # PID 로직 대신 직접 값을 입력받음
            self.inst.write(f"VOLT {voltage:.3f}")
            print(f"   ⚡ 전압 설정: {voltage:.3f} V")
            return True
        except Exception as e:
            print(f"   ❌ 전압 설정 실패: {e}")
            return False

    def output_on(self):
        """출력 켜기 (OUTP ON)"""
        try:
            self.inst.write("OUTP ON")
            print("   🔥 히터 출력 ON")
            return True
        except Exception as e:
            print(f"   ❌ 출력 ON 실패: {e}")
            return False

    def output_off(self):
        """출력 끄기 (OUTP OFF)"""
        try:
            self.inst.write("OUTP OFF")
            print("   ❄️ 히터 출력 OFF")
            return True
        except Exception as e:
            print(f"   ❌ 출력 OFF 실패: {e}")
            return False

    def disconnect(self):
        """
        [기존 코드 유지] _cleanup 메소드의 로직을 그대로 적용
        """
        print("\n[Heater] 연결 해제 및 안전 종료 중...")
        if self.inst:
            try:
                # 원본 코드의 cleanup 순서 준수
                self.inst.write("VOLT 0.0")
                time.sleep(0.5)

                self.inst.write("OUTP OFF")
                time.sleep(0.5)

                self.inst.write("*RST")
                self.inst.write("*CLS")

                # Close connection
                self.inst.close()
            except Exception as e:
                print(f"   ⚠️ 히터 종료 중 에러 발생 (무시됨): {e}")
                pass

        if self.rm:
            try:
                self.rm.close()
            except:
                pass

        print("   ✅ 히터 연결 해제 완료")


# =================================================================================
# 테스트용
# =================================================================================
if __name__ == "__main__":
    CONFIG = {
        "visa_address": "ASRL3::INSTR",
        "current_limit_amp": 2.0
    }
    heater = HeaterController(CONFIG)
    if heater.connect():
        # 테스트: 1V 인가 후 3초 대기 후 종료
        heater.set_voltage(1.0)
        heater.output_on()
        time.sleep(3)
        heater.disconnect()