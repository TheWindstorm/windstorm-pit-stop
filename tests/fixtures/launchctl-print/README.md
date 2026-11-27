# launchctl print, as GitHub's macOS runner images answer it

What `launchctl print gui/<uid>/<label>` printed for one made-up launch agent on each of GitHub's standard
macOS runner images, recorded 2026-11-25. The bytes are as printed, unchanged; each file is named by the
image and its macOS version:

- `macos-15-intel-15.7.9.txt`: image macos-15-intel, macOS 15.7.9 (x86_64)
- `macos-15-15.8.2.txt`: image macos-15, macOS 15.8.2 (arm64)
- `macos-26-intel-26.6.1.txt`: image macos-26-intel, macOS 26.6.1 (x86_64)
- `macos-26-26.7.2.txt`: image macos-26, macOS 26.7.2 (arm64)

The agent: `org.example.trial.print`, its arguments `/bin/sh`, `-c`, `sleep 300`, and one made-up variable,
`TRIAL_VALUE` = `made up`. These versions are named for the form of the print only (one argument to a line
after two tabs; the job's own environment block beside the inherited and default ones); they are not a list
of the macOS versions Pit Stop runs on. Read by `test_G12_49_launchd_parsed_reads_the_runner_images_prints`.
