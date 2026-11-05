# Сборка

Требования: Windows, Python 3.12+ и установленный `pip`.

```powershell
python -m pip install pyinstaller bsdiff4
pyinstaller --noconfirm --onedir --windowed --name WDH_RU_Patcher patcher/patcher.py
```

После сборки положи рядом с `WDH_RU_Patcher.exe`:

- `patcher/manifest.json`
- `patcher/payload/`
- `README.md`
- `LICENSE`

```powershell
$dst = "dist\WDH_RU_Patcher"
Copy-Item "patcher\manifest.json" $dst
Copy-Item "patcher\payload" $dst -Recurse
Copy-Item "README.md","LICENSE" $dst
python tools\make_checksums.py $dst
Compress-Archive -Path $dst -DestinationPath "WDH_RU_Patcher_vX.Y.Z.zip"
```

Проверь патчер на **копии чистой игры**, а не на единственной установке:

```powershell
.\dist\WDH_RU_Patcher\WDH_RU_Patcher.exe --check --game "S:\Test_Run"
.\dist\WDH_RU_Patcher\WDH_RU_Patcher.exe --install --yes --game "S:\Test_Run"
.\dist\WDH_RU_Patcher\WDH_RU_Patcher.exe --uninstall --yes --game "S:\Test_Run"
```

После удаления файлы игры должны совпадать с чистой версией, а `WDH_RU_Backup` и временные папки — исчезнуть.
