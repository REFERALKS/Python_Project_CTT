# emnist_viz_vscode.py
"""
Автономный скрипт для VS Code / локального запуска.
Работает без Plotly — использует matplotlib.
"""

import os
import numpy as np
import torch
from torch import nn, optim
import torch.nn.functional as F
from torchvision import datasets, transforms
from torch.utils.data import DataLoader
import matplotlib.pyplot as plt
import tqdm

# Параметры
DATA_ROOT = "D:\проекты\python_project\models"
MODEL_FILE = "emnist_autoencoder_vscode.pth"
BATCH = 128
EPOCHS = 12
LR = 1e-3
LATENT_DIM = 2

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("Device:", device)

# Трансформации для EMNIST (корректировка ориентации)
transform = transforms.Compose([
    transforms.ToTensor(),
    transforms.Lambda(lambda x: x.transpose(1,2).flip(1)),
])

os.makedirs(DATA_ROOT, exist_ok=True)
train_ds = datasets.EMNIST(root=DATA_ROOT, split='letters', train=True, download=True, transform=transform)
test_ds  = datasets.EMNIST(root=DATA_ROOT, split='letters', train=False, download=True, transform=transform)

train_loader = DataLoader(train_ds, batch_size=BATCH, shuffle=True, num_workers=0, pin_memory=True)
test_loader  = DataLoader(test_ds, batch_size=1024, shuffle=False, num_workers=0, pin_memory=True)

# Модель (тот же автоэнкодер)
class Autoencoder(nn.Module):
    def __init__(self, latent_dim=2):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 32, 3, padding=1), nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1), nn.ReLU(),
            nn.AdaptiveAvgPool2d(1)
        )
        self.flatten = nn.Flatten()
        self.fc_mu = nn.Linear(128, latent_dim)
        self.decoder = nn.Sequential(
            nn.Linear(latent_dim, 128), nn.ReLU(),
            nn.Linear(128, 256), nn.ReLU(),
            nn.Linear(256, 28*28), nn.Sigmoid()
        )

    def forward(self, x):
        f = self.features(x)
        f = self.flatten(f)
        z = self.fc_mu(f)
        out = self.decoder(z).view(-1,1,28,28)
        return out, z

model = Autoencoder(latent_dim=LATENT_DIM).to(device)
optimizer = optim.AdamW(model.parameters(), lr=LR)
criterion = nn.MSELoss()

# Загрузка весов если есть
if os.path.exists(MODEL_FILE):
    model.load_state_dict(torch.load(MODEL_FILE, map_location=device))
    print("Loaded model from", MODEL_FILE)
else:
    print("No weights found. Training from scratch.")

# Функция тренировки
def train(epochs=EPOCHS):
    model.train()
    for epoch in range(1, epochs+1):
        running = 0.0
        pbar = tqdm.tqdm(train_loader, desc=f"Epoch {epoch}/{epochs}")
        for xb, _ in pbar:
            xb = xb.to(device)
            optimizer.zero_grad()
            out, z = model(xb)
            loss = criterion(out, xb)
            loss.backward()
            optimizer.step()
            running += loss.item() * xb.size(0)
            pbar.set_postfix(loss=loss.item())
        avg = running / len(train_loader.dataset)
        print(f"Epoch {epoch} avg_loss={avg:.6f}")
    torch.save(model.state_dict(), MODEL_FILE)
    print("Saved weights to", MODEL_FILE)

# Запускаем тренировку только если весов нет
if not os.path.exists(MODEL_FILE):
    train()

# Собираем латентные векторы для визуализации
model.eval()
feats = []
labels = []
imgs = []
with torch.no_grad():
    for xb, yb in DataLoader(test_ds, batch_size=2048, shuffle=False):
        xb = xb.to(device)
        out, z = model(xb)
        feats.append(z.cpu().numpy())
        labels.append((yb.numpy()-1))
        imgs.append(xb.cpu().numpy())
feats = np.vstack(feats)
labels = np.concatenate(labels)
imgs = np.vstack(imgs)

# Подвыборка для быстрого рендера
N = min(len(feats), 3000)
idx = np.random.choice(len(feats), size=N, replace=False)
Z = feats[idx]
L = labels[idx]
IM = imgs[idx]

# Matplotlib визуализация: scatter слева, картинка справа
plt.ion()
fig, ax = plt.subplots(figsize=(10,8))
sc = ax.scatter(Z[:,0], Z[:,1], c=L, cmap='tab20', s=8, alpha=0.7)
plt.colorbar(sc, label='labels')
ax.set_title("EMNIST latent — hover to decode")
ax.set_xlabel("z0"); ax.set_ylabel("z1")
img_ax = fig.add_axes([0.78, 0.6, 0.2, 0.3])
img_ax.axis('off')
img_handle = img_ax.imshow(np.zeros((28,28)), cmap='gray', vmin=0, vmax=1)

# Определяем callback движения мыши
def on_move(event):
    if event.inaxes == ax and event.xdata is not None:
        x, y = float(event.xdata), float(event.ydata)
        # ограничение координат по квантилям, чтобы не выходить за область интереса
        x = np.clip(x, Z[:,0].min()-1, Z[:,0].max()+1)
        y = np.clip(y, Z[:,1].min()-1, Z[:,1].max()+1)
        try:
            with torch.no_grad():
                decoded = model.decoder(torch.tensor([[x,y]], dtype=torch.float32).to(device))
            decoded = decoded.view(28,28).cpu().numpy()
        except Exception:
            decoded = np.zeros((28,28))
        img_handle.set_data(decoded)
        fig.canvas.draw_idle()

fig.canvas.mpl_connect('motion_notify_event', on_move)
plt.show(block=True)
