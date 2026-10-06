`pgs-canvas.mkv` is FFmpeg's 23 KiB PGS regression sample:
https://samples.ffmpeg.org/sub/PGS/supsample.mkv

The 720×480 video has a 1280×720 subtitle canvas. Tests verify that bitmap
subtitles render inside the video and preserve its duration. No network is
needed to run the test.
