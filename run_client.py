from pathlib import Path
import sys
from whisper_live.client import TranscriptionClient
import argparse


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', '-p',
                          type=int,
                          default=9090,
                          help="Websocket port to run the server on.")
    parser.add_argument('--server', '-s',
                          type=str,
                          default='localhost',
                          help='hostname or ip address of server')
    parser.add_argument('--files', '-f',
                          type=str,
                          nargs='+',
                          help='Files to transcribe, separated by spaces. '
                              'If not provided, will use microphone input.')
    parser.add_argument('--output_file', '-o',
                          type=str,
                          default='./output_recording.wav',
                          help='output recording filename, only used for microphone input.')
    parser.add_argument('--model', '-m',
                          type=str,
                          default='base',
                          help='Model to use for transcription, e.g., "tiny, small.en, large-v3".')
    parser.add_argument('--lang', '-l',
                          type=str,
                          default='en',
                          help='Language code for transcription, e.g., "en" for English.')
    parser.add_argument('--translate', '-t',
                          action='store_true',
                          default= 'true',
                          help='Enable translation of the transcription output.')
    parser.add_argument('--mute_audio_playback', '-a',
                          action='store_true',
                          help='Mute audio playback during transcription.') 
    parser.add_argument('--save_output_recording', '-r',
                          action='store_true',
                          help='Save the output recording, only used for microphone input.')
    parser.add_argument('--enable_translation',
                          action='store_true',
                          default='false',
                          help='Enable translation of the transcription output.')
    parser.add_argument('--target_language', '-tl',
                          type=str,
                          default='hi',
                          help='Target language for translation, e.g., "fr" for French.')

    args = parser.parse_args()

    transcription_client = TranscriptionClient(host="10.11.71.68", port=9090)
    transcription_client()
