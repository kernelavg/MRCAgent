# Third-party notices

## Piper TTS

The Windows preview bundles `piper-tts==1.8.0`, licensed under GPL-3.0-or-later. Source and distribution details for this exact version: https://pypi.org/project/piper-tts/1.8.0/ and https://github.com/OHF-Voice/piper1-gpl . The application source and build scripts are in `source/` in this repository. Other dependencies, including ONNX Runtime, eSpeak NG, Python, PyInstaller and pywebview, have their own licenses and corresponding distribution obligations; review the versions used by the build and their terms.

## Ruslan voice

The packaged `ru_RU-ruslan-medium` voice is downloaded by `source/download_voice.py` from the upstream Piper voices repository. Its [model card](https://huggingface.co/rhasspy/piper-voices/blob/main/ru/ru_RU/ruslan/medium/MODEL_CARD) identifies the training dataset as the [RUSLAN corpus](https://ruslan-corpus.github.io/), licensed [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). Check the applicable model and data terms, especially before commercial use or redistribution.

Publishing source here does not itself establish compliance with all distribution licenses. Review all dependencies and provide any additional corresponding source, notices and terms required for the binary.
