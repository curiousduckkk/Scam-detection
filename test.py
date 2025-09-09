import sounddevice as sd
import numpy as np

duration = 5  # seconds
fs = 44100    # sampling rate
print("Recording...")
myrecording = sd.rec(int(duration * fs), samplerate=fs, channels=1)
sd.wait()
print("Recording finished! Max amplitude:", np.max(np.abs(myrecording)))
