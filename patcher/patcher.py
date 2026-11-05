from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import bsdiff4

APP_NAME = "WDH RU Patcher"
MANIFEST_NAME = "manifest.json"
BACKUP_NAME = "WDH_RU_Backup"
TEMP_PREFIX = "WDH_RU_TMP_"
GAME_EXE = "WithTheDevilishHer.exe"
CREATE_NO_WINDOW = 0x08000000


def setup_console() -> None:
    if os.name == "nt":
        try:
            subprocess.run(["chcp", "65001"], capture_output=True, creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


class PatcherError(Exception):
    pass


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def human_size(n: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} ГБ"


def load_manifest() -> dict:
    p = app_dir() / MANIFEST_NAME
    if not p.is_file():
        raise PatcherError(f"Не найден {MANIFEST_NAME} рядом с патчером.")
    with open(p, "r", encoding="utf-8") as f:
        m = json.load(f)
    if m.get("format") != 1:
        raise PatcherError("Неподдерживаемый формат манифеста.")
    return m


def resolve_game(arg: str | None) -> Path:
    candidates = []
    if arg:
        candidates.append(Path(arg))
    candidates.extend([Path.cwd(), app_dir(), app_dir().parent])
    for c in candidates:
        try:
            c = c.resolve()
            if c.is_file():
                c = c.parent
            if (c / GAME_EXE).is_file():
                return c
            children = [x for x in c.iterdir() if x.is_dir() and (x / GAME_EXE).is_file()]
            if len(children) == 1:
                return children[0].resolve()
        except (OSError, PermissionError):
            continue
    raise PatcherError(
        "Не найден WithTheDevilishHer.exe. Укажи папку с игрой "
        "(туда, где лежит WithTheDevilishHer.exe)."
    )


def game_running() -> bool:
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=15,
        ).stdout
    except Exception:
        return False
    return GAME_EXE.lower() in out.lower()


def ensure_game_closed() -> None:
    if game_running():
        raise PatcherError("Игра запущена. Полностью закрой её и повтори.")


def payload_path(entry: dict) -> Path:
    p = app_dir() / entry["payload"]
    if not p.is_file():
        raise PatcherError(f"Не найден файл патча: {entry['payload']}")
    return p


def verify_payloads(manifest: dict) -> None:
    for e in manifest["files"]:
        p = payload_path(e)
        if p.stat().st_size != e["payload_size"]:
            raise PatcherError(f"Размер патча не совпадает: {e['payload']}")
        if sha256(p) != e["payload_sha256"]:
            raise PatcherError(f"SHA-256 патча не совпадает: {e['payload']}")


def file_state(game: Path, entry: dict) -> tuple[str, int | None, str | None]:
    p = game / entry["path"]
    if not p.is_file():
        return "missing", None, None
    if entry.get("method") == "range":
        offset = int(entry["offset"])
        size = int(entry["range_size"])
        if p.stat().st_size < offset + size:
            return "unknown", size, None
        with open(p, "rb") as f:
            f.seek(offset)
            data = f.read(size)
        h = hashlib.sha256(data).hexdigest()
        if len(data) == size and h == entry["original_sha256"]:
            return "original", size, h
        if len(data) == size and h == entry["patched_sha256"]:
            return "patched", size, h
        return "unknown", size, h
    size = p.stat().st_size
    h = sha256(p)
    if size == entry["original_size"] and h == entry["original_sha256"]:
        return "original", size, h
    if size == entry["patched_size"] and h == entry["patched_sha256"]:
        return "patched", size, h
    return "unknown", size, h


def classify(game: Path, manifest: dict) -> tuple[str, list[dict], str | None]:
    exe = game / manifest["game"]["exe"]
    if not exe.is_file():
        return "unsupported", [], f"Не найден {manifest['game']['exe']}"
    if exe.stat().st_size != manifest["game"]["exe_size"]:
        return "unsupported", [], "Размер exe не совпадает с поддерживаемой версией."
    if sha256(exe) != manifest["game"]["exe_sha256"]:
        return "unsupported", [], "Версия игры не совпадает с поддерживаемой (exe изменён или обновлён)."
    rows = []
    for e in manifest["files"]:
        state, size, h = file_state(game, e)
        rows.append({"entry": e, "state": state, "size": size, "sha256": h})
        if state in ("missing", "unknown"):
            return "unsupported", rows, f"Файл не совпадает с чистой версией: {e['path']} ({state})"
    states = {r["state"] for r in rows}
    if states == {"original"}:
        return "clean", rows, None
    if states == {"patched"}:
        return "installed", rows, None
    return "mixed", rows, "Часть файлов уже пропатчена, часть — нет."


def backup_path(game: Path, manifest: dict) -> Path:
    return game / manifest.get("backup_dir", BACKUP_NAME)


def read_backup_manifest(bk: Path) -> dict:
    p = bk / "backup_manifest.json"
    if not p.is_file():
        raise PatcherError("Резервная копия повреждена: нет backup_manifest.json.")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def create_backup(game: Path, manifest: dict, rows: list[dict]) -> Path:
    bk = backup_path(game, manifest)
    if bk.exists():
        raise PatcherError(
            f"Папка {bk.name} уже существует. Запусти удаление или удали её вручную после проверки."
        )
    saved = []
    try:
        bk.mkdir(parents=True)
        files_dir = bk / "files"
        files_dir.mkdir()
        for r in rows:
            e = r["entry"]
            src = game / e["path"]
            item = {
                "path": e["path"],
                "original_size": e["original_size"],
                "original_sha256": e["original_sha256"],
            }
            if e.get("method") == "range":
                offset = int(e["offset"])
                size = int(e["range_size"])
                dst = files_dir / (e["path"] + ".range")
                dst.parent.mkdir(parents=True, exist_ok=True)
                with open(src, "rb") as f:
                    f.seek(offset)
                    data = f.read(size)
                with open(dst, "wb") as f:
                    f.write(data)
                if len(data) != size or sha256(dst) != e["original_sha256"]:
                    raise PatcherError(f"Ошибка резервной копии диапазона: {e['path']}")
                item["range_offset"] = offset
                item["range_size"] = size
            else:
                dst = files_dir / e["path"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                if sha256(dst) != e["original_sha256"]:
                    raise PatcherError(f"Ошибка копирования в резервную копию: {e['path']}")
            item["backup"] = str(dst.relative_to(bk)).replace("\\", "/")
            saved.append(item)
        data = {
            "format": 1,
            "patch_version": manifest["patch_version"],
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": saved,
        }
        with open(bk / "backup_manifest.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return bk
    except PermissionError as e:
        shutil.rmtree(bk, ignore_errors=True)
        raise PatcherError(
            "Нет прав на запись в папку игры. Распакуй патчер в обычную папку "
            "(например, Рабочий стол или Загрузки), закрой игру и проверь антивирус."
        ) from e
    except Exception:
        shutil.rmtree(bk, ignore_errors=True)
        raise


def remove_temp_dirs(game: Path) -> None:
    for p in game.glob(TEMP_PREFIX + "*"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


def make_temp_dir(game: Path) -> Path:
    return Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=str(game)))


def make_patched_file(game: Path, entry: dict, tmp: Path) -> None:
    src = game / entry["path"]
    p = payload_path(entry)
    if entry["method"] == "bsdiff":
        bsdiff4.file_patch(str(src), str(tmp), str(p))
    elif entry["method"] == "replace":
        shutil.copyfile(p, tmp)
    elif entry["method"] == "range":
        return
    else:
        raise PatcherError(f"Неизвестный метод патча: {entry['method']}")
    if tmp.stat().st_size != entry["patched_size"]:
        raise PatcherError(f"Размер после патча не совпадает: {entry['path']}")
    if sha256(tmp) != entry["patched_sha256"]:
        raise PatcherError(f"SHA-256 после патча не совпадает: {entry['path']}")


def apply_range_patch(game: Path, entry: dict) -> None:
    p = payload_path(entry)
    data = p.read_bytes()
    size = int(entry["range_size"])
    if len(data) != size or hashlib.sha256(data).hexdigest() != entry["patched_sha256"]:
        raise PatcherError(f"Патч диапазона повреждён: {entry['path']}")
    target = game / entry["path"]
    with open(target, "r+b") as f:
        f.seek(int(entry["offset"]))
        f.write(data)
    state, _, _ = file_state(game, entry)
    if state != "patched":
        raise PatcherError(f"Диапазон не прошёл проверку: {entry['path']}")


def install(game: Path, manifest: dict, assume_yes: bool = False, dry_run: bool = False, log=print) -> int:
    verify_payloads(manifest)
    state, rows, reason = classify(game, manifest)
    log(f"Папка игры: {game}")
    log(f"Состояние: {state}" + (f" ({reason})" if reason else ""))
    if state == "installed":
        log("Перевод уже установлен. Для удаления запусти патчер с --uninstall.")
        return 0 if dry_run else 1
    if state != "clean":
        raise PatcherError(reason or "Игра не в чистом состоянии; установка отменена.")
    ensure_game_closed()
    if not assume_yes and not dry_run:
        ans = input("Установить перевод? [y/N] ").strip().lower()
        if ans not in ("y", "yes", "д", "да"):
            log("Отменено.")
            return 1
    if dry_run:
        tmp = make_temp_dir(game)
        try:
            for r in rows:
                if r["entry"].get("method") == "range":
                    continue
                make_patched_file(game, r["entry"], tmp / Path(r["entry"]["path"]).name)
            log("Проверка успешна: все патчи применяются, файлы не изменены.")
            return 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    bk = create_backup(game, manifest, rows)
    log(f"Резервная копия: {bk}")
    tmp = make_temp_dir(game)
    try:
        for r in rows:
            e = r["entry"]
            if e.get("method") == "range":
                continue
            out = tmp / (str(len(list(tmp.iterdir()))) + "_" + Path(e["path"]).name)
            make_patched_file(game, e, out)
            r["_tmp"] = out
        ensure_game_closed()
        for r in rows:
            if r["entry"].get("method") == "range":
                apply_range_patch(game, r["entry"])
            else:
                os.replace(r["_tmp"], game / r["entry"]["path"])
        final_state, _, final_reason = classify(game, manifest)
        if final_state != "installed":
            raise PatcherError(final_reason or "Итоговая проверка не прошла.")
        log("Готово. Перевод установлен.")
        log(f"Удаление: запусти этот же патчер и выбери «Удалить перевод».")
        return 0
    except Exception:
        log("Ошибка установки. Восстанавливаю исходные файлы...")
        restore_backup(game, manifest, bk, log)
        shutil.rmtree(bk, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        remove_temp_dirs(game)


def restore_backup(game: Path, manifest: dict, bk: Path, log=print) -> None:
    data = read_backup_manifest(bk)
    tmp = make_temp_dir(game)
    try:
        for item in data["files"]:
            src = bk / item["backup"]
            if not src.is_file():
                raise PatcherError(f"В резервной копии нет файла: {item['backup']}")
            if sha256(src) != item["original_sha256"]:
                raise PatcherError(f"Повреждён файл резервной копии: {item['backup']}")
            if "range_offset" in item:
                target = game / item["path"]
                if not target.is_file():
                    raise PatcherError(f"Не найден файл для восстановления: {item['path']}")
                payload = src.read_bytes()
                if len(payload) != int(item["range_size"]):
                    raise PatcherError(f"Повреждён диапазон резервной копии: {item['backup']}")
            else:
                out = tmp / (str(len(list(tmp.iterdir()))) + "_" + Path(item["path"]).name)
                shutil.copyfile(src, out)
                if sha256(out) != item["original_sha256"]:
                    raise PatcherError(f"Ошибка подготовки восстановления: {item['path']}")
                item["_tmp"] = out
        for item in data["files"]:
            if "range_offset" in item:
                continue
            os.replace(item["_tmp"], game / item["path"])
        for item in data["files"]:
            if "range_offset" in item:
                target = game / item["path"]
                with open(target, "r+b") as f:
                    f.seek(int(item["range_offset"]))
                    f.write((bk / item["backup"]).read_bytes())
            else:
                p = game / item["path"]
                if sha256(p) != item["original_sha256"]:
                    raise PatcherError(f"Восстановление не прошло проверку: {item['path']}")
        for item in data["files"]:
            if "range_offset" in item:
                target = game / item["path"]
                with open(target, "rb") as f:
                    f.seek(int(item["range_offset"]))
                    data = f.read(int(item["range_size"]))
                if hashlib.sha256(data).hexdigest() != item["original_sha256"]:
                    raise PatcherError(f"Диапазон не прошёл проверку: {item['path']}")
        log("Исходные файлы восстановлены и проверены.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def uninstall(game: Path, manifest: dict, assume_yes: bool = False, log=print) -> int:
    bk = backup_path(game, manifest)
    if not bk.is_dir():
        raise PatcherError("Резервная копия не найдена. Удаление невозможно; проверь файлы игры в Steam.")
    ensure_game_closed()
    data = read_backup_manifest(bk)
    state, rows, reason = classify(game, manifest)
    log(f"Папка игры: {game}")
    log(f"Состояние: {state}" + (f" ({reason})" if reason else ""))
    if state == "clean":
        if not assume_yes:
            ans = input("Игра уже чистая. Удалить оставшуюся резервную копию? [y/N] ").strip().lower()
            if ans not in ("y", "yes", "д", "да"):
                log("Отменено.")
                return 1
        shutil.rmtree(bk, ignore_errors=True)
        remove_temp_dirs(game)
        log("Резервная копия удалена. Лишних файлов не осталось.")
        return 0
    if state == "unsupported":
        raise PatcherError(reason or "Файлы игры изменены; удаление отменено.")
    if not assume_yes:
        ans = input("Удалить перевод и вернуть исходные файлы? [y/N] ").strip().lower()
        if ans not in ("y", "yes", "д", "да"):
            log("Отменено.")
            return 1
    restore_backup(game, manifest, bk, log)
    shutil.rmtree(bk, ignore_errors=True)
    remove_temp_dirs(game)
    final_state, _, final_reason = classify(game, manifest)
    if final_state != "clean":
        raise PatcherError(final_reason or "После удаления игра не в исходном состоянии.")
    log("Перевод удалён. Игра вернулась в исходное состояние.")
    return 0


def print_check(game: Path, manifest: dict, log=print) -> int:
    verify_payloads(manifest)
    state, rows, reason = classify(game, manifest)
    log(f"{manifest['patch_name']} {manifest['patch_version']}")
    log(f"Папка игры: {game}")
    log(f"Состояние: {state}" + (f" — {reason}" if reason else ""))
    for r in rows:
        e = r["entry"]
        log(f"  {e['path']}: {r['state']}")
    if state == "clean":
        log("Игра подходит для установки.")
        return 0
    if state == "installed":
        log("Перевод установлен; доступно удаление.")
        return 0
    return 2


def gui(main_game: str | None = None) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    manifest = load_manifest()
    root = tk.Tk()
    root.title(f"{APP_NAME} {manifest['patch_version']}")
    root.geometry("760x560")
    root.minsize(680, 480)

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Папка с игрой:").pack(anchor="w")
    path_var = tk.StringVar(value=main_game or "")
    path_row = ttk.Frame(frame)
    path_row.pack(fill="x", pady=(0, 8))
    path_entry = ttk.Entry(path_row, textvariable=path_var)
    path_entry.pack(side="left", fill="x", expand=True)

    def browse():
        p = filedialog.askdirectory(title="Выбери папку с WithTheDevilishHer.exe")
        if p:
            path_var.set(p)

    ttk.Button(path_row, text="Обзор...", command=browse).pack(side="left", padx=(6, 0))

    status_var = tk.StringVar(value="Проверь папку с игрой.")
    ttk.Label(frame, textvariable=status_var, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))

    info = (
        "Патч не содержит файлов игры. Он применяется только к твоей копии.\n"
        "Перед установкой создаётся видимая резервная копия WDH_RU_Backup.\n"
        "Сеть, реестр, службы и права администратора не используются."
    )
    ttk.Label(frame, text=info, justify="left").pack(anchor="w", pady=(0, 8))

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(0, 8))
    check_btn = ttk.Button(buttons, text="Проверить", width=18)
    install_btn = ttk.Button(buttons, text="Установить перевод", width=20)
    uninstall_btn = ttk.Button(buttons, text="Удалить перевод", width=18)
    check_btn.pack(side="left")
    install_btn.pack(side="left", padx=6)
    uninstall_btn.pack(side="left")

    log_box = tk.Text(frame, height=18, wrap="word", state="disabled")
    log_box.pack(fill="both", expand=True)

    def log(msg: str):
        def add():
            log_box.configure(state="normal")
            log_box.insert("end", str(msg) + "\n")
            log_box.see("end")
            log_box.configure(state="disabled")
        root.after(0, add)

    def get_game():
        p = path_var.get().strip()
        try:
            return resolve_game(p or None)
        except PatcherError as e:
            messagebox.showerror(APP_NAME, str(e))
            return None

    def refresh():
        p = path_var.get().strip()
        if not p:
            status_var.set("Выбери папку с игрой.")
            return
        try:
            g = resolve_game(p)
            state, rows, reason = classify(g, manifest)
            path_var.set(str(g))
            if state == "clean":
                status_var.set("Готово к установке: чистая версия.")
                install_btn.configure(state="normal")
                uninstall_btn.configure(state="disabled")
            elif state == "installed":
                status_var.set("Перевод установлен. Доступно удаление.")
                install_btn.configure(state="disabled")
                uninstall_btn.configure(state="normal")
            elif state == "mixed":
                status_var.set("Смешанное состояние: переустанови игру или восстанови бэкап.")
                install_btn.configure(state="disabled")
                uninstall_btn.configure(state="normal")
            else:
                status_var.set(reason or "Неподдерживаемая версия.")
                install_btn.configure(state="disabled")
                uninstall_btn.configure(state="disabled")
        except Exception as e:
            status_var.set(str(e))
            install_btn.configure(state="disabled")
            uninstall_btn.configure(state="disabled")

    def run_task(name, fn):
        g = get_game()
        if g is None:
            return
        check_btn.configure(state="disabled")
        install_btn.configure(state="disabled")
        uninstall_btn.configure(state="disabled")
        status_var.set(f"{name}...")

        def worker():
            try:
                rc = fn(g)
                if rc == 0:
                    log(f"{name}: готово.")
                else:
                    log(f"{name}: отменено.")
            except Exception as e:
                log(f"ОШИБКА: {e}")
                err = str(e)
                root.after(0, lambda: messagebox.showerror(APP_NAME, err))
            finally:
                root.after(0, lambda: (refresh(), check_btn.configure(state="normal")))

        threading.Thread(target=worker, daemon=True).start()

    def do_check():
        g = get_game()
        if g is None:
            return
        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.configure(state="disabled")
        print_check(g, manifest, log)
        refresh()

    def do_install():
        g = get_game()
        if g is None:
            return
        if not messagebox.askyesno(APP_NAME, "Установить перевод? Будет создана резервная копия."):
            return
        run_task("Установка", lambda game: install(game, manifest, assume_yes=True, log=log))

    def do_uninstall():
        g = get_game()
        if g is None:
            return
        if not messagebox.askyesno(APP_NAME, "Удалить перевод и вернуть исходные файлы?"):
            return
        run_task("Удаление", lambda game: uninstall(game, manifest, assume_yes=True, log=log))

    check_btn.configure(command=do_check)
    install_btn.configure(command=do_install)
    uninstall_btn.configure(command=do_uninstall)

    for e in manifest["files"]:
        log(f"{e['path']}  ({human_size(e['patched_size'])})")
    for note in manifest.get("notes", []):
        log(note)
    for note in manifest.get("untouched", []):
        log("Не изменяется: " + note)
    if path_var.get():
        root.after(100, refresh)
    root.mainloop()
    return 0


def main() -> int:
    setup_console()
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--game", "-g", help="Папка с игрой")
    parser.add_argument("--check", action="store_true", help="Только проверить состояние")
    parser.add_argument("--dry-run", action="store_true", help="Проверить применение патчей без изменений")
    parser.add_argument("--install", action="store_true", help="Установить перевод")
    parser.add_argument("--uninstall", action="store_true", help="Удалить перевод")
    parser.add_argument("--yes", "-y", action="store_true", help="Не спрашивать подтверждение")
    args = parser.parse_args()
    try:
        manifest = load_manifest()
        if not (args.check or args.dry_run or args.install or args.uninstall):
            return gui(args.game)
        game = resolve_game(args.game)
        if args.check:
            return print_check(game, manifest)
        if args.dry_run:
            return install(game, manifest, assume_yes=True, dry_run=True)
        if args.install:
            return install(game, manifest, assume_yes=args.yes, dry_run=False)
        if args.uninstall:
            return uninstall(game, manifest, assume_yes=args.yes)
    except PatcherError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        return 3


if __name__ == "__main__":
    if getattr(sys, "frozen", False) and len(sys.argv) == 1:
        try:
            raise SystemExit(gui())
        except Exception:
            import tkinter.messagebox as mb
            mb.showerror(APP_NAME, traceback.format_exc())
            raise SystemExit(3)
    raise SystemExit(main())
