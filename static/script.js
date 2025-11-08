let currentDownloadId = null;
let progressInterval = null;

const videoUrlInput = document.getElementById('videoUrl');
const clearBtn = document.getElementById('clearBtn');
const downloadBtn = document.getElementById('downloadBtn');
const progressSection = document.getElementById('progressSection');
const errorSection = document.getElementById('errorSection');
const successSection = document.getElementById('successSection');
const statusIcon = document.getElementById('statusIcon');
const statusMessage = document.getElementById('statusMessage');
const statusFilename = document.getElementById('statusFilename');
const progressFill = document.getElementById('progressFill');
const progressText = document.getElementById('progressText');
const detailStatus = document.getElementById('detailStatus');
const detailProgress = document.getElementById('detailProgress');
const errorMessage = document.getElementById('errorMessage');
const successFilename = document.getElementById('successFilename');

// Обработчик нажатия Enter в поле ввода
videoUrlInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        startDownload();
    }
});

// Обработчик изменения текста в поле ввода
videoUrlInput.addEventListener('input', () => {
    toggleClearButton();
});

// Обработчик кнопки очистки
clearBtn.addEventListener('click', () => {
    videoUrlInput.value = '';
    videoUrlInput.focus();
    toggleClearButton();
    hideAllSections();
});

// Обработчик кнопки скачивания
downloadBtn.addEventListener('click', startDownload);

function toggleClearButton() {
    if (videoUrlInput.value.trim().length > 0) {
        clearBtn.style.display = 'flex';
    } else {
        clearBtn.style.display = 'none';
    }
}

function startDownload() {
    const url = videoUrlInput.value.trim();
    
    if (!url) {
        showError('Пожалуйста, введите URL');
        return;
    }
    
    // Валидация URL
    try {
        new URL(url);
    } catch (e) {
        showError('Некорректный URL');
        return;
    }
    
    // Скрываем предыдущие сообщения
    hideAllSections();
    showProgress();
    
    // Блокируем кнопку
    downloadBtn.disabled = true;
    downloadBtn.querySelector('.btn-text').style.display = 'none';
    downloadBtn.querySelector('.btn-loader').style.display = 'block';
    
    // Отправляем запрос на сервер
    fetch('/api/download', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ url: url })
    })
    .then(response => response.json())
    .then(data => {
        currentDownloadId = data.download_id;
        startProgressPolling();
    })
    .catch(error => {
        showError(`Ошибка подключения: ${error.message}`);
        resetButton();
    });
}

function startProgressPolling() {
    if (progressInterval) {
        clearInterval(progressInterval);
    }
    
    progressInterval = setInterval(async () => {
        try {
            const response = await fetch(`/api/progress/${currentDownloadId}`);
            const data = await response.json();
            
            updateProgress(data);
            
            if (data.status === 'completed' || data.status === 'error') {
                clearInterval(progressInterval);
                progressInterval = null;
                
                if (data.status === 'completed') {
                    showSuccess(data.filename);
                } else {
                    showError(data.message);
                }
                
                resetButton();
            }
        } catch (error) {
            console.error('Ошибка получения прогресса:', error);
        }
    }, 500); // Обновляем каждые 500мс
}

function updateProgress(data) {
    const progress = data.progress || 0;
    
    // Обновляем прогресс-бар
    progressFill.style.width = `${progress}%`;
    progressText.textContent = `${progress.toFixed(1)}%`;
    
    // Обновляем сообщение
    statusMessage.textContent = data.message || 'Загрузка...';
    
    // Обновляем имя файла
    if (data.filename) {
        statusFilename.textContent = data.filename;
    }
    
    // Обновляем детали
    detailStatus.textContent = getStatusText(data.status);
    detailProgress.textContent = `${progress.toFixed(1)}%`;
    
    // Обновляем иконку статуса
    switch(data.status) {
        case 'downloading':
            statusIcon.textContent = '⏳';
            break;
        case 'completed':
            statusIcon.textContent = '✅';
            break;
        case 'error':
            statusIcon.textContent = '❌';
            break;
        default:
            statusIcon.textContent = '⏳';
    }
}

function getStatusText(status) {
    const statusMap = {
        'downloading': 'Загрузка',
        'completed': 'Завершено',
        'error': 'Ошибка',
        'started': 'Начато'
    };
    return statusMap[status] || 'Неизвестно';
}

function showProgress() {
    progressSection.style.display = 'block';
    errorSection.style.display = 'none';
    successSection.style.display = 'none';
    
    // Сбрасываем прогресс
    progressFill.style.width = '0%';
    progressText.textContent = '0%';
    statusMessage.textContent = 'Подготовка...';
    statusFilename.textContent = '';
    detailStatus.textContent = 'Ожидание...';
    detailProgress.textContent = '0%';
    statusIcon.textContent = '⏳';
}

function showError(message) {
    hideAllSections();
    errorSection.style.display = 'block';
    errorMessage.textContent = message;
}

function showSuccess(filename) {
    hideAllSections();
    successSection.style.display = 'block';
    successFilename.textContent = filename || 'Видео успешно скачано';
}

function hideAllSections() {
    progressSection.style.display = 'none';
    errorSection.style.display = 'none';
    successSection.style.display = 'none';
}

function resetButton() {
    downloadBtn.disabled = false;
    downloadBtn.querySelector('.btn-text').style.display = 'block';
    downloadBtn.querySelector('.btn-loader').style.display = 'none';
    currentDownloadId = null;
}

// Очистка при размонтировании
window.addEventListener('beforeunload', () => {
    if (progressInterval) {
        clearInterval(progressInterval);
    }
});

