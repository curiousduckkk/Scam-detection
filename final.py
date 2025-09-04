import sounddevice as sd
import numpy as np
import threading
import time
import os
import queue
import logging
from faster_whisper import WhisperModel
import datetime

logging.basicConfig(level=logging.INFO)

class LiveTranscriber:
    def __init__(self, disable_vad=False):
        self.rate = 16000
        self.chunk_duration = 5  # seconds - shorter for more responsive transcription
        self.chunk_samples = self.chunk_duration * self.rate
        self.transcript = []
        self.translated_transcript = []
        self.frames = []
        self.transcription_callback = None
        self.translation_callback = None
        self.stop_event = threading.Event()
        self.output_srt = "output.srt"
        self.translation_srt = "translated.srt"
        self.disable_vad = disable_vad
        
        # Initialize faster-whisper model directly
        print("Loading Whisper model...")
        self.model = WhisperModel("small", device="cpu", compute_type="int8")
        print("Model loaded successfully!")
        if disable_vad:
            print("VAD disabled - will transcribe all audio")

    def clear_screen(self):
        """Clear the terminal screen"""
        os.system('cls' if os.name == 'nt' else 'clear')

    def print_transcript(self, text_list, translated=True):
        """Print transcript text"""
        prefix = "[TRANSLATED]" if translated else "[ORIGINAL]"
        for text in text_list:
            if text.strip():
                print(f"{prefix} {text.strip()}")

    def format_time(self, seconds):
        """Convert seconds to SRT time format"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}".replace('.', ',')

    def create_srt_file(self, segments, filename):
        """Create SRT subtitle file"""
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                for i, segment in enumerate(segments, 1):
                    start_time = self.format_time(segment['start'])
                    end_time = self.format_time(segment['end'])
                    f.write(f"{i}\n")
                    f.write(f"{start_time} --> {end_time}\n")
                    f.write(f"{segment['text'].strip()}\n\n")
        except Exception as e:
            print(f"Error creating SRT file {filename}: {e}")

    def process_transcription(self, segments, translated=True):
        """Process transcribed segments"""
        if not segments:
            return
            
        new_segments = []
        current_time = len(self.transcript) * self.chunk_duration if not translated else len(self.translated_transcript) * self.chunk_duration
        
        for segment in segments:
            seg_dict = {
                "text": segment.text.strip(),
                "start": segment.start + current_time,
                "end": segment.end + current_time,
                "completed": True
            }
            new_segments.append(seg_dict)
            
            if translated:
                self.translated_transcript.append(seg_dict)
            else:
                self.transcript.append(seg_dict)

        # Call callbacks if provided
        # text = " ".join([seg["text"] for seg in new_segments])
        # if translated and self.translation_callback:
        #     try:
        #         self.translation_callback(text, new_segments)
        #     except Exception as e:
        #         print(f"[WARN] translation_callback error: {e}")
        # elif not translated and self.transcription_callback:
        #     try:
        #         self.transcription_callback(text, new_segments)
        #     except Exception as e:
        #         print(f"[WARN] transcription_callback error: {e}")

        # Display live results
        self.display_results()

    def display_results(self):
        """Display live transcription results"""
        self.clear_screen()
        print("=== LIVE TRANSCRIPTION ===")
        print("Press Ctrl+C to stop recording\n")
        
        print("ORIGINAL:")
        recent_original = self.transcript[-5:] if len(self.transcript) > 5 else self.transcript
        self.print_transcript([seg["text"] for seg in recent_original])
        
        print("\nTRANSLATION:")
        recent_translated = self.translated_transcript[-5:] if len(self.translated_transcript) > 5 else self.translated_transcript
        self.print_transcript([seg["text"] for seg in recent_translated], translated=True)
        
        print(f"\nTotal segments: {len(self.transcript)} original, {len(self.translated_transcript)} translated")

    def transcribe_chunk(self, audio_chunk):
        """Transcribe a chunk of audio"""
        try:
            # Check audio level for debugging
            audio_level = np.abs(audio_chunk).mean()
            max_level = np.abs(audio_chunk).max()
            print(f"Audio level - Mean: {audio_level:.6f}, Max: {max_level:.6f}")
            
            # If audio is too quiet, skip transcription
            if max_level < 0.001:
                print("Audio too quiet, skipping...")
                return
            
            # Transcribe in original language with relaxed VAD settings
            if self.disable_vad:
                segments, info = self.model.transcribe(
                    audio_chunk, 
                    language="hi",
                    task="transcribe",
                    vad_filter=False
                )
            else:
                segments, info = self.model.transcribe(
                    audio_chunk, 
                    language="hi",  # Change this to your source language
                    task="transcribe",
                    vad_filter=True,
                    vad_parameters=dict(
                        min_silence_duration_ms=100,
                        speech_pad_ms=30
                    )
                )
            
            segments_list = list(segments)
            if segments_list:
                print(f"Found {len(segments_list)} segments")
                self.process_transcription(segments_list, translated=False)
                
                # Also get translation
                if self.disable_vad:
                    translated_segments, _ = self.model.transcribe(
                        audio_chunk,
                        language="en",
                        task="translate",
                        vad_filter=False
                    )
                else:
                    translated_segments, _ = self.model.transcribe(
                        audio_chunk,
                        language="en",  # Source language
                        task="translate",  # This translates to English
                        vad_filter=False
                        # ,
                        # vad_parameters=dict(
                        #     min_silence_duration_ms=100,
                        #     speech_pad_ms=30,
                        #     threshold=0.1
                        # )
                    )
                
                translated_list = list(translated_segments)
                if translated_list:
                    self.process_transcription(translated_list, translated=True)
            else:
                print("No speech segments detected")
                    
        except Exception as e:
            print(f"Transcription error: {e}")

    def audio_callback(self, indata, frames, time, status):
        """Audio input callback"""
        if status:
            print(f"Audio status: {status}")
        
        # Convert int16 to float32 and normalize
        audio_data = indata.flatten().astype(np.float32) / 32768.0
        self.frames.extend(audio_data)
        
        # Process when we have enough samples
        if len(self.frames) >= self.chunk_samples:
            chunk = np.array(self.frames[:self.chunk_samples])
            self.frames = self.frames[self.chunk_samples:]  # Keep remaining samples
            
            # Process transcription in a separate thread to avoid blocking audio
            threading.Thread(target=self.transcribe_chunk, args=(chunk,), daemon=True).start()

    def record_loop(self):
        """Main recording loop"""
        print("Starting audio recording...")
        print("Speak into your microphone. Press Ctrl+C to stop.\n")
        
        try:
            with sd.InputStream(
                samplerate=self.rate,
                channels=1,
                dtype=np.int16,
                callback=self.audio_callback,
                blocksize=1024
            ):
                while not self.stop_event.is_set():
                    time.sleep(0.1)
                    
        except KeyboardInterrupt:
            print("\nStopping recording...")
            self.stop_event.set()
        except Exception as e:
            print(f"Recording error: {e}")

    def save_results(self):
        """Save transcription results to SRT files"""
        if self.transcript:
            self.create_srt_file(self.transcript, self.output_srt)
            print(f"Saved original transcript to {self.output_srt}")
        
        if self.translated_transcript:
            self.create_srt_file(self.translated_transcript, self.translation_srt)
            print(f"Saved translated transcript to {self.translation_srt}")
        
        if not self.transcript and not self.translated_transcript:
            print("No transcription data to save.")

    def run(self):
        """Run the live transcriber"""
        try:
            self.record_loop()
        finally:
            self.save_results()
            print("Transcription completed.")


def main():
    # You can disable VAD if it's being too aggressive
    transcriber = LiveTranscriber(disable_vad=False)  # Try with VAD disabled first
    transcriber.run()


if __name__ == "__main__":
    main()