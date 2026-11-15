Install on each PC that runs POS PPK Processor, in this order:

1. TrimbleCFGUpdate.exe  (Trimble Office Configuration Utility - antenna/receiver lists; Convert to RINEX crashes without it)
2. convertToRinex_v4_0_1_10_sign.msi  (Trimble Convert to RINEX 4.0.1.10, signed by Trimble Inc.)
   Installs to C:\Program Files (x86)\Trimble\ConvertToRinex\convertToRinex.exe
3. RTKLIB_EX_2.5.1.zip  (rtklibexplorer, GitHub) - already unpacked into ..\tools\rtklib\ (rnx2rtkp.exe). Nothing to install.
4. Run ..\launch.bat
