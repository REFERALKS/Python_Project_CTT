# AutoEncoder.py
import os
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torchvision import datasets, transforms
from torch.utils.data import DataLoader
import matplotlib.pyplot as plt
import tqdm

# ============================
# Настройки
# ============================
device = 'cuda' if torch.cuda.is_available() else 'cpu'
print("Device:", device)

seed = 42
torch.manual_seed(seed)
np.random.seed(seed)

data_dir = './data'                # где будет MNIST
models_dir = './models'            # где сохранять веса
os.makedirs(models_dir, exist_ok=True)
model_path = os.path.join(models_dir, 'mnist_autoencoder.pth')

batch_size = 128
dot_batch = 5000
latent_dim = 2       # для визуализации 2D
num_epochs = 10      # если треним
lr = 1e-3
num_workers = 0      # Windows: 0

# ============================
# Данные
# ============================
transform = transforms.ToTensor()
train_dataset = datasets.MNIST(root=data_dir, train=True, download=True, transform=transform)
train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=num_workers)
dot_loader = DataLoader(train_dataset, batch_size=dot_batch, shuffle=True, num_workers=num_workers)

# ============================
# Модель (архитектура, совместимая с ранним чекпоинтом)
# encoder: 784 -> 256 -> 64 -> latent_dim
# decoder: latent_dim -> 64 -> 256 -> 784
# ============================
class Autoencoder(nn.Module):
    def __init__(self, latent_dim=2):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Flatten(),                 # [B,1,28,28] -> [B, 784]
            nn.Linear(28*28, 512),       # 784 -> 256
            nn.ReLU(inplace=True),
            nn.Linear(512, 64),          # 256 -> 64
            nn.ReLU(inplace=True),
            nn.Linear(64, latent_dim)    # 64 -> latent_dim
        )
        self.decoder = nn.Sequential(
            nn.Linear(latent_dim, 64),
            nn.ReLU(inplace=True),
            nn.Linear(64, 512),
            nn.ReLU(inplace=True),
            nn.Linear(512, 28*28),
            nn.Sigmoid()
        )

    def forward(self, x):
        z = self.encoder(x)
        out = self.decoder(z).view(-1, 1, 28, 28)
        return out, z

model = Autoencoder(latent_dim=latent_dim).to(device)
optimizer = optim.AdamW(model.parameters(), lr=lr)
criterion = nn.MSELoss()

# ============================
# Функция безопасной загрузки state_dict (полная или частичная)
# ============================
def safe_load(model, path, map_location='cpu'):
    if not os.path.exists(path):
        print("Чекпоинт не найден:", path)
        return False
    checkpoint = torch.load(path, map_location=map_location)
    # если в чекпоинте обёртка
    if isinstance(checkpoint, dict) and 'model_state_dict' in checkpoint:
        checkpoint = checkpoint['model_state_dict']

    model_state = model.state_dict()
    loaded = {}
    skipped = []
    for k, v in checkpoint.items():
        if k in model_state and v.size() == model_state[k].size():
            loaded[k] = v
        else:
            skipped.append((k, tuple(v.size()), tuple(model_state.get(k, torch.empty(0)).size())))
    # применяем загруженные
    model_state.update(loaded)
    model.load_state_dict(model_state)
    print(f"Загружено параметров: {len(loaded)}. Пропущено (несовпадение): {len(skipped)}.")
    if skipped:
        print("Примеры пропущенных:")
        for s in skipped[:10]:
            print(" ", s)
    return True

# ============================
# Попытка загрузить веса (если есть)
# ============================
loaded_ok = safe_load(model, model_path, map_location=device)
if loaded_ok:
    print("Веса применены (возможно частично).")
else:
    print("Веса не загружены — будет обучение и последующее сохранение.")

# ============================
# Обучение (если веса не были полностью применены)
# ============================
def train_model(epochs=num_epochs):
    model.train()
    for epoch in range(1, epochs + 1):
        running = 0.0
        pbar = tqdm.tqdm(train_loader, desc=f"Epoch {epoch}/{epochs}")
        for xb, _ in pbar:
            xb = xb.to(device)
            optimizer.zero_grad()
            out, _ = model(xb)
            loss = criterion(out, xb)
            loss.backward()
            optimizer.step()
            running += loss.item() * xb.size(0)
            pbar.set_postfix(loss=f"{loss.item():.6f}")
        avg = running / len(train_loader.dataset)
        print(f"Epoch {epoch} avg loss: {avg:.6f}")
    torch.save(model.state_dict(), model_path)
    print("Сохранены веса в", model_path)

# Решаем: если веса были загружены частично (пропущены ключи), лучше дообучить.
# Для простоты: если файл не найден — мы треним. Если найден — считаем, что тренить не нужно.
if not os.path.exists(model_path):
    train_model(num_epochs)
else:
    # если был частичный load (мог пропустить слои) — рекомендуется дообучить.
    # Здесь простая эвристика: если некоторые слои были случайно инициализированы,
    # пользователь может вручную вызвать train_model().
    pass

# ============================
# Функции визуализации
# ============================
def get_latent_cloud():
    model.eval()
    with torch.no_grad():
        for xb, labels in dot_loader:
            xb = xb.to(device)
            _, z = model(xb)
            return z.cpu().numpy(), labels.numpy()
    return None, None

def show_scatter_with_decoder(n_points=4000, figsize=(12,8)):
    z_arr, labels = get_latent_cloud()
    if z_arr is None:
        print("Не получилось получить латентные представления.")
        return
    xs = z_arr[:, 0]; ys = z_arr[:, 1]
    if len(xs) > n_points:
        idx = np.random.choice(len(xs), n_points, replace=False)
        xs = xs[idx]; ys = ys[idx]; labels = labels[idx]

    fig = plt.figure(figsize=figsize)
    ax_main = fig.add_axes([0.06, 0.08, 0.68, 0.88])   # большой axes
    ax_small = fig.add_axes([0.78, 0.62, 0.18, 0.28])  # маленький декодер

    sc = ax_main.scatter(xs, ys, c=labels, cmap='tab10', s=10, alpha=0.85)
    ax_main.set_xlabel("Закодированное измерение 1")
    ax_main.set_ylabel("Закодированное измерение 2")
    ax_main.set_title("Точечный график закодированных изображений")
    plt.colorbar(sc, ax=ax_main, label='Метки (цифры)')

    img = np.zeros((28,28))
    im_handle = ax_small.imshow(img, cmap='gray', vmin=0, vmax=1)
    ax_small.set_title("Декод")
    ax_small.axis('off')

    def decode_xy(x, y):
        z = torch.zeros((1, latent_dim), dtype=torch.float32, device=device)
        # если latent_dim > 2 — заполним остальные нулями
        z[0, 0] = float(x); z[0, 1] = float(y)
        with torch.no_grad():
            out = model.decoder(z).view(28,28).cpu().numpy()
        return out

    def on_move(event):
        if event.inaxes == ax_main and event.xdata is not None and event.ydata is not None:
            img_new = decode_xy(event.xdata, event.ydata)
            im_handle.set_data(img_new)
            fig.canvas.draw_idle()

    fig.canvas.mpl_connect('motion_notify_event', on_move)

    plt.show()  # блокирует выполнение до закрытия окна
    return

# ============================
# Вызов визуализации (можно вызвать несколько раз)
# ============================
show_scatter_with_decoder(n_points=4000, figsize=(12,8))
# show_scatter_with_decoder(n_points=3000, figsize=(10,6))  # открыть ещё окно, если нужно

print("Готово.")
