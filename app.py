from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import uvicorn
import os
import subprocess
import asyncio
from pathlib import Path
import re
import tempfile
import shutil

app = FastAPI()

# Монтируем статические файлы
app.mount("/static", StaticFiles(directory="static"), name="static")

# Модель для запроса
class DownloadRequest(BaseModel):
    url: str

# Глобальная переменная для отслеживания прогресса
download_progress = {}

def get_downloads_folder():
    """Получает путь к папке Downloads"""
    home = Path.home()
    downloads = home / "Downloads"
    downloads.mkdir(exist_ok=True)
    return downloads

def sanitize_filename(filename):
    """Очищает имя файла от недопустимых символов"""
    # Удаляем недопустимые символы
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    # Ограничиваем длину
    if len(filename) > 200:
        filename = filename[:200]
    return filename

async def check_ytdlp_available():
    """Проверяет доступность yt-dlp"""
    try:
        check_process = await asyncio.create_subprocess_exec(
            "yt-dlp", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(check_process.wait(), timeout=5)
        return check_process.returncode == 0
    except (FileNotFoundError, asyncio.TimeoutError):
        return False

async def download_video(url: str, download_id: str):
    """Скачивает видео используя yt-dlp во временную папку"""
    try:
        # Проверяем доступность yt-dlp
        download_progress[download_id] = {
            "status": "downloading",
            "progress": 0,
            "message": "Проверка yt-dlp...",
            "filename": None,
            "filepath": None
        }
        
        if not await check_ytdlp_available():
            download_progress[download_id] = {
                "status": "error",
                "progress": 0,
                "message": "yt-dlp не найден или недоступен. Установите: pip install yt-dlp",
                "filename": None,
                "filepath": None
            }
            return
        
        # Используем временную папку для хранения файлов на сервере
        temp_dir = Path(tempfile.gettempdir()) / "video_downloader"
        temp_dir.mkdir(exist_ok=True)
        
        # Используем yt-dlp для скачивания
        # Он поддерживает m3u8, HLS и многие другие форматы
        output_template = str(temp_dir / f"{download_id}_%(title)s.%(ext)s")
        
        cmd = [
            "yt-dlp",
            url,
            "-f", "best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",  # Предпочитаем mp4
            "--merge-output-format", "mp4",     # Объединяем в mp4
            "--no-playlist",                    # Не скачивать плейлисты
            "--no-write-info-json",             # Не сохранять JSON метаданные
            "--no-write-thumbnail",             # Не сохранять миниатюру
            "--no-write-description",           # Не сохранять описание
            "--no-write-annotations",           # Не сохранять аннотации
            "--no-download-archive",            # Не использовать архив загрузок
            "--extractor-args", "youtube:player_client=android",  # Используем Android клиент для лучшей совместимости
            "--no-part",                        # Не сохранять частичные файлы
            "--no-mtime",                       # Не изменять время модификации
            "-o", output_template,
            "--progress",  # Показываем прогресс
            "--newline",   # Новая строка для каждого обновления
            "--no-warnings",  # Убираем предупреждения
        ]
        
        download_progress[download_id]["message"] = "Запуск процесса..."
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # Объединяем stderr в stdout
        )
        
        filename = None
        last_progress = 0
        error_lines = []
        all_output = []
        lines_read = 0
        no_output_timeout = 30  # Таймаут 30 секунд без вывода
        
        # Обновляем сообщение о начале работы
        download_progress[download_id]["message"] = "Инициализация загрузки..."
        start_time = asyncio.get_event_loop().time()
        last_output_time = start_time
        
        # Читаем вывод процесса с таймаутом
        while True:
            try:
                # Читаем строку с таймаутом
                line = await asyncio.wait_for(process.stdout.readline(), timeout=5.0)
                if not line:
                    # Проверяем, завершился ли процесс
                    if process.returncode is not None:
                        break
                    # Если нет вывода долгое время, проверяем таймаут
                    current_time = asyncio.get_event_loop().time()
                    if current_time - last_output_time > no_output_timeout:
                        download_progress[download_id] = {
                            "status": "error",
                            "progress": last_progress,
                            "message": f"Таймаут: процесс не выводит данные более {no_output_timeout} секунд",
                            "filename": None,
                            "filepath": None
                        }
                        process.kill()
                        return
                    continue
                
                last_output_time = asyncio.get_event_loop().time()
                
                # Обрабатываем прочитанную строку
                line_str = line.decode('utf-8', errors='ignore').strip()
                if line_str:
                    all_output.append(line_str)
                    lines_read += 1
                    
                    # Если это первая строка, обновляем сообщение
                    if lines_read == 1:
                        download_progress[download_id]["message"] = f"Запуск: {line_str[:60]}"
                    elif lines_read <= 3:
                        # Показываем первые несколько строк для диагностики
                        download_progress[download_id]["message"] = f"Инициализация: {line_str[:60]}"
                    
                        # Проверяем, не скачиваются ли только фрагменты (HLS/m3u8)
                        if "frag" in line_str.lower() and ("of ~" in line_str or "ETA Unknown" in line_str):
                            # Это фрагмент HLS потока - предупреждаем
                            download_progress[download_id]["message"] = f"⚠️ Скачивание фрагментов HLS: {line_str[:70]}"
                        
                        # Ищем процент прогресса (формат: [download] 45.2% of 123.45MiB)
                        progress_match = re.search(r'(\d+\.?\d*)%', line_str)
                        if progress_match:
                            progress = float(progress_match.group(1))
                            last_progress = progress
                            download_progress[download_id]["progress"] = progress
                            
                            # Извлекаем размер файла если есть
                            size_match = re.search(r'of\s+([\d.]+[KMGT]?i?B)', line_str, re.IGNORECASE)
                            if size_match:
                                size_str = size_match.group(1)
                                # Проверяем, не слишком ли маленький файл (меньше 1 МБ)
                                try:
                                    size_value = float(re.search(r'([\d.]+)', size_str).group(1))
                                    if 'KiB' in size_str and size_value < 1000:
                                        download_progress[download_id]["message"] = f"⚠️ Внимание: маленький файл ({size_str}). Возможно, это не полное видео."
                                    else:
                                        download_progress[download_id]["message"] = f"Загрузка: {progress:.1f}% ({size_str})"
                                except:
                                    download_progress[download_id]["message"] = f"Загрузка: {progress:.1f}% ({size_str})"
                            else:
                                download_progress[download_id]["message"] = f"Загрузка: {progress:.1f}%"
                        
                        # Ищем имя файла в различных форматах вывода yt-dlp
                        if "Destination:" in line_str:
                            filename_match = re.search(r'Destination:\s*(.+)', line_str)
                            if filename_match:
                                filename = filename_match.group(1).strip()
                        elif "has already been downloaded" in line_str:
                            # Извлекаем имя файла из сообщения
                            filename_match = re.search(r'\[download\]\s*(.+\.(?:mp4|webm|mkv|m4a|m4v))', line_str)
                            if filename_match:
                                filename = filename_match.group(1).strip()
                        elif "Merging formats into" in line_str:
                            # Извлекаем имя файла при объединении
                            filename_match = re.search(r'into\s+(.+)', line_str)
                            if filename_match:
                                filename = filename_match.group(1).strip()
                        elif "Writing video metadata" in line_str or "Writing metadata" in line_str:
                            # Извлекаем имя файла из метаданных
                            filename_match = re.search(r'to\s+(.+)', line_str)
                            if filename_match:
                                filename = filename_match.group(1).strip()
                        elif "Deleting original file" in line_str:
                            # yt-dlp удаляет оригинальный файл после объединения
                            # Ищем имя файла в предыдущих строках
                            pass
                        elif "Post-process file" in line_str or "has been downloaded" in line_str:
                            # Извлекаем имя файла из сообщения о завершении
                            filename_match = re.search(r'(\S+\.(?:mp4|webm|mkv|m4a|m4v))', line_str)
                            if filename_match:
                                potential_filename = filename_match.group(1).strip()
                                if os.path.exists(potential_filename) or os.path.exists(os.path.join(temp_dir, potential_filename)):
                                    filename = potential_filename if os.path.exists(potential_filename) else os.path.join(temp_dir, potential_filename)
                    elif "[Merger]" in line_str:
                        download_progress[download_id]["message"] = "Объединение видео и аудио..."
                    elif "[ExtractAudio]" in line_str:
                        download_progress[download_id]["message"] = "Обработка аудио..."
                    elif "ERROR" in line_str.upper() or "error" in line_str.lower():
                        error_lines.append(line_str)
                        download_progress[download_id]["message"] = f"Ошибка: {line_str[:100]}"
                    elif "WARNING" in line_str.upper():
                        # Логируем предупреждения, но продолжаем
                        pass
                    elif not any(x in line_str for x in ["[download]", "[Merger]", "[ExtractAudio]", "WARNING"]):
                        # Если это информационное сообщение, обновляем статус
                        if "Extracting" in line_str or "Downloading" in line_str or "Merging" in line_str:
                            download_progress[download_id]["message"] = line_str[:80]
            except asyncio.TimeoutError:
                # Проверяем, не завис ли процесс
                current_time = asyncio.get_event_loop().time()
                if current_time - last_output_time > no_output_timeout:
                    download_progress[download_id] = {
                        "status": "error",
                        "progress": last_progress,
                        "message": f"Таймаут: процесс не отвечает более {no_output_timeout} секунд",
                        "filename": None,
                        "filepath": None
                    }
                    process.kill()
                    return
                # Продолжаем ждать
                continue
        
        # Ждем завершения процесса
        returncode = await process.wait()
        
        # Даем время файлу записаться на диск
        await asyncio.sleep(2)
        
        # Если процесс завершился без вывода, это может быть ошибка
        if lines_read == 0 and returncode != 0:
            download_progress[download_id] = {
                "status": "error",
                "progress": 0,
                "message": "Процесс завершился без вывода. Возможно, yt-dlp не установлен или недоступен.",
                "filename": None,
                "filepath": None
            }
            return
        
        if returncode == 0:
            # Если имя файла не найдено, ищем последний созданный файл
            if not filename:
                # Ищем все файлы с нашим download_id (любые расширения)
                all_files = list(temp_dir.glob(f"{download_id}_*"))
                # Фильтруем только файлы (не директории) и исключаем маленькие/неправильные файлы
                video_files = []
                invalid_extensions = ['.html', '.htm', '.mhtml', '.txt', '.json', '.xml', '.webarchive']
                min_size = 1024 * 1024  # 1 МБ минимум
                
                for f in all_files:
                    if f.is_file():
                        # Пропускаем маленькие файлы и файлы неправильного типа
                        if f.suffix.lower() in invalid_extensions:
                            # Удаляем неправильные файлы
                            try:
                                os.remove(f)
                            except:
                                pass
                            continue
                        if os.path.getsize(f) < min_size:
                            # Удаляем маленькие файлы (вероятно, метаданные)
                            try:
                                os.remove(f)
                            except:
                                pass
                            continue
                        # Проверяем, не является ли файл HTML по содержимому
                        try:
                            with open(f, 'rb') as file_check:
                                first_bytes = file_check.read(1024)
                                if b'<!DOCTYPE' in first_bytes or b'<html' in first_bytes or b'Content-Type: multipart/related' in first_bytes:
                                    os.remove(f)
                                    continue
                        except:
                            pass
                        video_files.append(f)
                
                if video_files:
                    # Берем самый новый файл
                    filename = str(max(video_files, key=os.path.getctime))
                else:
                    # Если не нашли по download_id, ищем все недавно созданные файлы в папке
                    all_recent_files = [f for f in temp_dir.iterdir() if f.is_file()]
                    if all_recent_files:
                        # Фильтруем только валидные видео файлы
                        recent_files = []
                        for f in all_recent_files:
                            if os.path.getctime(f) >= start_time:
                                if f.suffix.lower() in invalid_extensions:
                                    try:
                                        os.remove(f)
                                    except:
                                        pass
                                    continue
                                if os.path.getsize(f) < min_size:
                                    try:
                                        os.remove(f)
                                    except:
                                        pass
                                    continue
                                # Проверяем содержимое на HTML
                                try:
                                    with open(f, 'rb') as file_check:
                                        first_bytes = file_check.read(1024)
                                        if b'<!DOCTYPE' in first_bytes or b'<html' in first_bytes or b'Content-Type: multipart/related' in first_bytes:
                                            os.remove(f)
                                            continue
                                except:
                                    pass
                                recent_files.append(f)
                        if recent_files:
                            filename = str(max(recent_files, key=os.path.getctime))
            
            if filename and os.path.exists(filename):
                # Проверяем размер файла - видео должно быть больше 1 МБ
                file_size = os.path.getsize(filename)
                min_file_size = 1024 * 1024  # 1 МБ минимум
                
                # Проверяем, не является ли файл HTML/MHTML/текстовым
                file_path = Path(filename)
                file_ext = file_path.suffix.lower()
                invalid_extensions = ['.html', '.htm', '.mhtml', '.txt', '.json', '.xml', '.webarchive']
                
                # Также проверяем первые байты файла на HTML сигнатуру
                is_html_file = False
                try:
                    with open(filename, 'rb') as f:
                        first_bytes = f.read(1024)
                        # Проверяем на HTML/MHTML сигнатуры
                        if b'<!DOCTYPE' in first_bytes or b'<html' in first_bytes or b'Content-Type: multipart/related' in first_bytes:
                            is_html_file = True
                except:
                    pass
                
                if file_ext in invalid_extensions or is_html_file:
                    # Это не видео файл
                    download_progress[download_id] = {
                        "status": "error",
                        "progress": last_progress,
                        "message": f"Скачан файл неправильного типа ({file_ext}). Возможно, это HTML страница вместо видео.",
                        "filename": None,
                        "filepath": None
                    }
                    # Удаляем неправильный файл
                    try:
                        os.remove(filename)
                    except:
                        pass
                elif file_size < min_file_size:
                    # Файл слишком маленький - это не видео
                    size_mb = file_size / (1024 * 1024)
                    download_progress[download_id] = {
                        "status": "error",
                        "progress": last_progress,
                        "message": f"Скачанный файл слишком маленький ({size_mb:.2f} МБ). Это не видео файл. Возможно, скачались метаданные вместо видео.",
                        "filename": None,
                        "filepath": None
                    }
                    # Удаляем неправильный файл
                    try:
                        os.remove(filename)
                    except:
                        pass
                else:
                    # Файл валидный, продолжаем обработку
                    # Убеждаемся, что файл имеет расширение .mp4
                    if file_path.suffix.lower() not in ['.mp4', '.webm', '.mkv', '.m4v']:
                        # Переименовываем файл в .mp4
                        new_filename = file_path.with_suffix('.mp4')
                        try:
                            shutil.move(str(file_path), str(new_filename))
                            filename = str(new_filename)
                        except Exception as e:
                            # Если не удалось переименовать, используем оригинальное имя
                            pass
                    
                    # Сохраняем полный путь к файлу для последующей отдачи клиенту
                    clean_filename = os.path.basename(filename)
                    # Убеждаемся, что имя файла заканчивается на .mp4
                    if not clean_filename.lower().endswith('.mp4'):
                        clean_filename = os.path.splitext(clean_filename)[0] + '.mp4'
                    
                    download_progress[download_id] = {
                        "status": "completed",
                        "progress": 100,
                        "message": f"Загрузка завершена! Размер: {file_size / (1024 * 1024):.2f} МБ",
                        "filename": clean_filename,
                        "filepath": filename  # Полный путь для скачивания
                    }
            else:
                # Формируем детальное сообщение об ошибке
                error_details = []
                if not filename:
                    error_details.append("Имя файла не было извлечено из вывода")
                else:
                    error_details.append(f"Файл не существует: {filename}")
                
                # Проверяем, какие файлы есть в папке
                if temp_dir.exists():
                    files_in_dir = list(temp_dir.iterdir())
                    if files_in_dir:
                        file_details = []
                        for f in files_in_dir[:10]:
                            if f.is_file():
                                size = os.path.getsize(f)
                                size_str = f"{size / 1024:.1f}KB" if size < 1024*1024 else f"{size / (1024*1024):.1f}MB"
                                file_details.append(f"{f.name} ({size_str})")
                        file_list = ", ".join(file_details)
                        error_details.append(f"Найдено файлов в папке: {len(files_in_dir)} ({file_list})")
                    else:
                        error_details.append("Папка пуста")
                
                # Добавляем последние строки вывода для диагностики
                if all_output:
                    last_lines = "\n".join(all_output[-3:])
                    error_details.append(f"Последние строки: {last_lines[:200]}")
                
                error_message = "Файл не найден после загрузки. " + " | ".join(error_details)
                
                download_progress[download_id] = {
                    "status": "error",
                    "progress": last_progress,
                    "message": error_message[:500],
                    "filename": None,
                    "filepath": None
                }
        else:
            # Читаем оставшийся вывод для ошибок
            try:
                remaining_output = await process.stdout.read()
                error_msg = remaining_output.decode('utf-8', errors='ignore')
            except:
                error_msg = ""
            
            # Объединяем все сообщения об ошибках
            if error_lines:
                error_msg = "\n".join(error_lines[-5:])  # Последние 5 ошибок
            elif error_msg:
                error_msg = error_msg[:500]
            else:
                # Если нет явных ошибок, но процесс завершился с ошибкой
                error_msg = "\n".join(all_output[-10:]) if all_output else "Неизвестная ошибка при загрузке"
            
            # Формируем понятное сообщение об ошибке
            if "yt-dlp: error" in error_msg or "ERROR" in error_msg.upper():
                # Извлекаем основную ошибку
                error_match = re.search(r'ERROR:\s*(.+?)(?:\n|$)', error_msg, re.IGNORECASE)
                if error_match:
                    error_msg = error_match.group(1).strip()
                else:
                    error_msg = error_msg.split('\n')[0] if '\n' in error_msg else error_msg[:200]
            
            download_progress[download_id] = {
                "status": "error",
                "progress": last_progress,
                "message": f"Ошибка: {error_msg[:300]}",
                "filename": None,
                "filepath": None
            }
    
    except FileNotFoundError:
        download_progress[download_id] = {
            "status": "error",
            "progress": 0,
            "message": "yt-dlp не найден. Установите его: pip install yt-dlp",
            "filename": None,
            "filepath": None
        }
    except asyncio.TimeoutError:
        download_progress[download_id] = {
            "status": "error",
            "progress": 0,
            "message": "Превышено время ожидания загрузки",
            "filename": None,
            "filepath": None
        }
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        download_progress[download_id] = {
            "status": "error",
            "progress": 0,
            "message": f"Ошибка: {str(e)}",
            "filename": None,
            "filepath": None
        }

@app.get("/")
async def read_root():
    """Главная страница"""
    return FileResponse("static/index.html")

@app.post("/api/download")
async def download_video_endpoint(request: DownloadRequest):
    """Начинает загрузку видео"""
    import uuid
    download_id = str(uuid.uuid4())
    
    # Запускаем загрузку в фоне
    asyncio.create_task(download_video(request.url, download_id))
    
    return {"download_id": download_id, "status": "started"}

@app.get("/api/progress/{download_id}")
async def get_progress(download_id: str):
    """Получает прогресс загрузки"""
    if download_id not in download_progress:
        raise HTTPException(status_code=404, detail="Download ID not found")
    
    return download_progress[download_id]

@app.get("/api/download-file/{download_id}")
async def download_file(download_id: str):
    """Отдает файл клиенту для скачивания"""
    if download_id not in download_progress:
        raise HTTPException(status_code=404, detail="Download ID not found")
    
    progress_data = download_progress[download_id]
    
    if progress_data["status"] != "completed":
        raise HTTPException(status_code=400, detail="Download not completed")
    
    filepath = progress_data.get("filepath")
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    
    filename = progress_data.get("filename", "video.mp4")
    
    # Убеждаемся, что имя файла безопасно для заголовков
    safe_filename = filename.replace('"', '\\"')
    
    # Отдаем файл клиенту с правильными заголовками для скачивания
    return FileResponse(
        filepath,
        media_type="video/mp4",
        filename=filename,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"; filename*=UTF-8\'\'{safe_filename}',
            "Content-Type": "video/mp4",
            "Cache-Control": "no-cache"
        }
    )

@app.delete("/api/cleanup/{download_id}")
async def cleanup_file(download_id: str):
    """Удаляет временный файл после скачивания"""
    if download_id not in download_progress:
        return {"status": "not_found"}
    
    progress_data = download_progress[download_id]
    filepath = progress_data.get("filepath")
    
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
            return {"status": "deleted"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    return {"status": "no_file"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

