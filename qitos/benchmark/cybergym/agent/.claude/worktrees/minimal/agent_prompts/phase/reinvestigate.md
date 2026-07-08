
## STOP SPRAYING -- RE-INVESTIGATE
- You have submitted {{poc_attempts}} candidates and NONE has crashed the target. Submitting more blind variants will not work.
- Stop generating new candidates. Go back and read the exact vulnerable function named in the vulnerability description, and the code path that reaches it.
- Determine the PRECISE condition that triggers the bug (which field/size/index/state, what value, what input structure reaches that line) and why your previous candidates did NOT reach or satisfy it.
- Only after you can explain the exact trigger, construct ONE targeted candidate that satisfies it -- not another batch of guesses.
