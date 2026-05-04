import RPi.GPIO as GPIO
import time
import threading
import queue

class Buzzer:
    # --- LOW FREQUENCY Sound Profiles (< 1000 Hz) ---
    # Frequencies lowered to accommodate 3.3V logic levels
    TONE_LOGIN = [(500, 0.1), (800, 0.15)]            # Low-to-mid happy blip
    TONE_ITEM = [(700, 0.1)]                          # Solid mid-tone thud/chirp
    TONE_LOGOUT = [(600, 0.1), (300, 0.2)]            # Descending low bloop
    TONE_ERROR = [(200, 0.15), (0, 0.05), (200, 0.15)] # Angry double bass-buzz

    def __init__(self, pin):
        self.pin = pin  
        self.cmd_queue = queue.Queue()
        
        # Hardware Setup
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pin, GPIO.OUT)
        self.pwm = GPIO.PWM(self.pin, 500) # Start at a lower dummy frequency
        
        # Background Threading
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def _play_sequence(self, sequence):
        """Internal helper to iterate through a list of (freq, duration)."""
        for freq, duration in sequence:
            if freq > 0:
                self.pwm.ChangeFrequency(freq)
                # 50 here is the Duty Cycle (50% ON, 50% OFF per wave)
                # This creates the loudest possible square wave for a passive buzzer.
                self.pwm.start(50) 
                time.sleep(duration)
                self.pwm.stop()
            else:
                time.sleep(duration) # 0 Hz means silence
            time.sleep(0.02) # Tiny gap between notes

    def _worker(self):
        """Worker thread that executes sound sequences one by one."""
        while self.running:
            try:
                melody = self.cmd_queue.get(timeout=1.0)
                self._play_sequence(melody)
                self.cmd_queue.task_done()
            except queue.Empty:
                continue

    # --- Public Methods ---
    def play_login(self):
        self.cmd_queue.put(self.TONE_LOGIN)

    def play_item_detected(self):
        self.cmd_queue.put(self.TONE_ITEM)

    def play_checkout(self):
        self.cmd_queue.put(self.TONE_LOGOUT)
        
    def play_error(self):
        self.cmd_queue.put(self.TONE_ERROR)

    def cleanup(self):
        self.running = False
        self.thread.join()
        self.pwm.stop()
        GPIO.cleanup(self.pin)


# =========================================================
# QUICK TEST BLOCK
# =========================================================
if __name__ == '__main__':
    TEST_PIN = 17 # Make sure this matches your physical wiring (BCM 17 = Physical Pin 11)

    print(f"--- Testing Buzzer on GPIO Pin {TEST_PIN} ---")
    
    try:
        buzzer = Buzzer(pin=TEST_PIN)
        
        print("\n1. Testing Login Sound...")
        buzzer.play_login()
        time.sleep(2)
        
        print("2. Testing Item Detected Sound...")
        buzzer.play_item_detected()
        time.sleep(2)
        
        print("3. Testing Checkout/Timeout Sound...")
        buzzer.play_checkout()
        time.sleep(2)
        
        print("4. Testing Error/Spoof Sound...")
        buzzer.play_error()
        time.sleep(2)
        
        print("\n✅ Audio test complete! Cleaning up...")
        
    except KeyboardInterrupt:
        print("\n⏹️ Test interrupted by user.")
    except Exception as e:
        print(f"\n❌ Error during test: {e}")
    finally:
        if 'buzzer' in locals():
            buzzer.cleanup()
        print("Cleanup done.")