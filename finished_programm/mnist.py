import os
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import matplotlib.pyplot as plt
from torchvision import datasets, transforms
from torch.utils.data import DataLoader
import tqdm
import torch.optim as optim

# устройство
device = 'cuda' if torch.cuda.is_available() else 'cpu'

# безопасный путь (raw string чтобы не было invalid escape sequence)
transform = transforms.Compose([transforms.ToTensor()])
DATA_ROOT = r'D:\проекты\python_project\models'   # raw string
os.makedirs(DATA_ROOT, exist_ok=True)

# загрузка данных
mnist_train = datasets.MNIST(root=DATA_ROOT, train=True, download=True, transform=transform)
mnist_loader = DataLoader(mnist_train, batch_size=64, shuffle=True, num_workers=0)  # num_workers=0 для Windows

# определение модели
class Autoencoder(nn.Module):
    def __init__(self):
        super(Autoencoder, self).__init__()
        self.encoder = nn.Sequential(
            nn.Conv2d(1, 16, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.Conv2d(16, 32, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.Flatten(),
            nn.Linear(64 * 4 * 4, 2)
        )
        self.decoder = nn.Sequential(
            nn.Linear(2, 64),
            nn.ReLU(),
            nn.Linear(64, 128),
            nn.ReLU(),
            nn.Linear(128, 28 * 28),
            nn.Sigmoid()
        )

    def forward(self, x):
        enc = self.encoder(x)
        x = self.decoder(enc).view(-1, 1, 28, 28)
        return x, enc

# инициализация
model = Autoencoder().to(device)
optimizer = optim.AdamW(model.parameters(), lr=0.001)
model_path = 'mnist_autoencoder_d2.pth'
criterion = nn.MSELoss()

# Попытка безопасно загрузить веса
weights_loaded = False
if os.path.exists(model_path):
    try:
        model.load_state_dict(torch.load(model_path, map_location=device))
        weights_loaded = True
        print(f'Модель успешно загружена из {model_path}')
    except Exception as e:
        print(f'Ошибка при загрузке весов из {model_path}: {e}')
        weights_loaded = False
else:
    print(f'Файл весов {model_path} не найден — обучение будет выполнено.')

# функция потерь (как раньше)
def simple_eval(inputs, labels):
    outputs, _ = model(inputs)
    return criterion(inputs, outputs)

# тренировочный цикл (выполняется с той функцией оценки, которую передадут)
def train_me(num_epochs = 20, eval_func = simple_eval):
    model.train()
    for epoch in range(num_epochs):
        total_loss = 0.0
        tq = tqdm.tqdm(mnist_loader, desc=f'Epoch {epoch + 1}/{num_epochs}', unit='batches')
        for inputs, labels in tq:
            inputs = inputs.to(device)
            labels = labels.to(device)  # важно — метки должны быть на том же устройстве
            optimizer.zero_grad()
            loss = eval_func(inputs, labels)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
            tq.set_postfix(loss=f'{loss.item():.4f}')
        average_loss = total_loss / len(mnist_loader)
        print(f'Epoch [{epoch + 1}/{num_epochs}], Average Loss: {average_loss:.4f}')

# Если веса не загружены — делаем базовое обучение и сохраняем
if not weights_loaded:
    train_me(num_epochs=20, eval_func=simple_eval)
    torch.save(model.state_dict(), model_path)
    print(f'Веса сохранены в {model_path}')
else:
    print("Пропускаем базовое обучение — используются загруженные веса.")

# --- Настройка центров для "flower-like" loss (координаты для цифр 0..9) ---
scale = 5.0
angles = np.linspace(0, 2 * np.pi, 10, endpoint=False)
x = np.cos(angles) * scale
y = np.sin(angles) * scale
spot_centers = torch.stack([torch.from_numpy(x).float(), torch.from_numpy(y).float()], dim=1).to(device)  # [10,2]

# Определяем функцию потерь для энкодера
def encoder_loss(enc, labels, scale=scale):
    # enc: [B,2], labels: [B] (LongTensor) — индексы 0..9
    desired_values = spot_centers[labels]             # [B,2]
    distances = F.mse_loss(enc, desired_values, reduction='none').sum(dim=1)  # [B]
    mean_distance = distances.mean() / scale
    return mean_distance

# Определяем комбинированную функцию оценки (потерь) для обучения автоэнкодера
def eval_flower_like(inputs, labels):
    outputs, enc = model(inputs)
    rec_loss = criterion(inputs, outputs)
    enc_loss = encoder_loss(enc, labels)
    return rec_loss + enc_loss

# При необходимости — дообучаем модель с комбинированной функцией
fine_tune = True  # поставь False, чтобы пропустить дообучение
if fine_tune:
    print("Начинаем fine-tune с eval_flower_like...")
    train_me(num_epochs=20, eval_func=eval_flower_like)
    torch.save(model.state_dict(), model_path)
    print("Fine-tune завершён и веса сохранены.")

# --- Функция формирования облака точек (создаёт собственный DataLoader каждый вызов) ---
def make_dot_cloud(batch_size=5000):
    loader = DataLoader(mnist_train, batch_size=batch_size, shuffle=True, num_workers=0)
    images, labels = next(iter(loader))
    images = images.to(device)
    with torch.no_grad():
        dots = model.encoder(images).detach().cpu().numpy()
    return dots, labels.numpy()

# --- Визуализация с интерактивным декодером ---
def scatter_plot_with_coordinates():
    dots, labels = make_dot_cloud()
    fig, ax = plt.subplots(figsize=(10, 8))

    sc = ax.scatter(dots[:, 0], dots[:, 1], c=labels, cmap=plt.get_cmap('viridis'), alpha=0.7)

    img_ax = fig.add_axes([0.85, 0.65, 0.2, 0.2])
    img_handle = img_ax.imshow(np.zeros((28,28)), cmap='gray', vmin=0, vmax=1)
    img_ax.axis('off')

    ax.set_title('Точечный график закодированных изображений')
    ax.set_xlabel('Закодированное измерение 1')
    ax.set_ylabel('Закодированное измерение 2')
    plt.colorbar(sc, label='Метки (цифры)')

    # вычислим разумные границы латентов, чтобы масштаб курсора совпадал с обучением
    lat_min = dots.min(axis=0) - 1.0
    lat_max = dots.max(axis=0) + 1.0

    def on_mouse_move(event):
        if event.inaxes == ax and event.xdata is not None and event.ydata is not None:
            x, y = float(event.xdata), float(event.ydata)
            # безопасная нормализация в диапазон, где закодированы данные
            x = np.clip(x, lat_min[0], lat_max[0])
            y = np.clip(y, lat_min[1], lat_max[1])
            try:
                with torch.no_grad():
                    decoded = model.decoder(torch.tensor([[x, y]], dtype=torch.float32, device=device))
                decoded = decoded.view(28,28).cpu().numpy()
            except Exception:
                decoded = np.zeros((28,28))
            img_handle.set_data(decoded)
            fig.canvas.draw_idle()

    fig.canvas.mpl_connect('motion_notify_event', on_mouse_move)
    plt.show(block=True)

# Запуск визуализации
if __name__ == "__main__":
    scatter_plot_with_coordinates()
