# CH341 transmit tuning validation

Date: 2026-11-06. Preview 13 / Build 25.

## Reproduced problem

On Linux 5.4, the previous 4096-byte OUT buffer did not prevent the TTY core from splitting an
application's 4108-byte write into 2048 + 2048 + 12 byte requests. In one failed 921600-baud run,
the final 12-byte request completed after 2405 ms. The original flashing tool had already timed out
and started recovery; the delayed write response then arrived during the next command.
No USB short write was observed. This evidence establishes a delayed response, not where every
part of the network/USB delay originated.

## Generic transport change

`uta-ch341-tx2` retains the 4096-byte IN buffer, uses an 8192-byte OUT buffer and grows the serial
write FIFO to at least 8192 during port probe. `TTY_NO_WRITE_SPLIT` avoids the default TTY 2 KiB
fragmentation. The pinned upstream source and Linux 5.4 removal callback adaptation are retained.
The module does not recognize chips, firmware, flashing commands or acknowledgments.

The physical test candidate was `uta-ch341-tx2-test`; the packaged generated source is compared
against that candidate with only the module version changed. The final module is also built and
loaded on the target 5.4 kernel before delivery. Neither version replaces the system driver file.

## Physical result

- Linux 5.4.0-162-generic, macOS USB/IP over SSH, CH340 1a86:7523.
- Original BK7259 loader 4.1.7.261014250, 921600 baud, existing 4,723,744-byte image.
- External tool configuration: pre_erase 0, baud command delay 250 ms, switch delay 10 ms,
  response/read timeout 3000 ms, flash-ID wait 2 seconds and manual reset window 180 seconds.
  Tool response/address/CRC checks were retained. UTA does not apply this configuration itself.
- One complete run passed: runner 106.983 seconds, official tool 105.634 seconds, write stage 103 seconds.
  The tool reported `Download complete, all pass.` and `protect flash OK.`.
- Previous 115200 write stage: 433.586 seconds. The write-stage ratio is 4.21x; whole-run comparisons
  additionally include different reset wait times.
- Additional readback: 4096 bytes at 0x481000; the image's remaining 1056 bytes match exactly.
  This is tail verification, not full image byte-for-byte readback.
- 1358 completed USB OUT requests: zero errors, zero short writes, zero outstanding requests.
  1010 requests were 4108 bytes, without the former 2048-byte fragments. Completion median 64.782 ms,
  95th percentile 109.829 ms, maximum 370.145 ms.

An earlier candidate run stopped at 55% with an SSH connection failure and is not counted as a pass.
The image was not first erased to blank and the original tool may skip already-correct data.
One successful run is not evidence of long-term stability, all firmware functions, complete log
retention or performance on every USB bridge, kernel or network. The module remains opt-in and
must be enabled again after reboot. No private firmware, raw logs, host settings or kernel binaries
are part of the release.
