# Collaborative collage

This is a source code for installation presented on EAGx Prague 2025.

The whole program was quick & dirty hacked and it was only tested on 
my laptop and the installation laptop.

Requirements:
* Python and requirements in "requirements.txt"
* OpenAI API key that has `gpt-image-1` access (it does not work with Dall-E models because v2 ignore masks and v3 does not support masks at all).

## How to start the program

```bash
cd src
python main.py --fullscreen
```

It starts the control application on first screen and projector part on the second screen.
