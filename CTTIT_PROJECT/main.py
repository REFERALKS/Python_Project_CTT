import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider

# параметры
g = 9.81       # ускорение свободного падения
y0 = 0.0       # начальная высота
L = 2.0        # длина ствола (м)

# функция расчёта траектории
def trajectory(force, theta, mass):
    v0 = np.sqrt(2 * force * L / mass)  # начальная скорость через силу, массу и длину ствола
    theta_rad = np.radians(theta)
    vx = v0 * np.cos(theta_rad)
    vy = v0 * np.sin(theta_rad)
    t_flight = 2 * vy / g
    t = np.linspace(0, t_flight, 300)
    x = vx * t
    y = y0 + vy * t - 0.5 * g * t**2
    return x, y

# создаём большое окно
fig, ax = plt.subplots(figsize=(12, 6))
plt.subplots_adjust(left=0.1, bottom=0.35)
line, = ax.plot([], [], lw=2, color='blue')
ax.set_xlim(0, 500)
ax.set_ylim(0, 200)
ax.set_xlabel("Дальность, м")
ax.set_ylabel("Высота, м")
ax.set_title("Интерактивная траектория снаряда")
ax.grid(True)

# слайдеры
ax_force = plt.axes([0.1, 0.25, 0.65, 0.03])
ax_angle = plt.axes([0.1, 0.18, 0.65, 0.03])
ax_mass = plt.axes([0.1, 0.11, 0.65, 0.03])

slider_force = Slider(ax_force, "Сила (Н)", 1, 500, valinit=100)
slider_angle = Slider(ax_angle, "Угол (°)", 10, 80, valinit=45)
slider_mass = Slider(ax_mass, "Масса (кг)", 0.01, 2.0, valinit=0.1)

# обновление траектории
def update(val):
    force = slider_force.val
    theta = slider_angle.val
    mass = slider_mass.val
    x, y = trajectory(force, theta, mass)
    line.set_xdata(x)
    line.set_ydata(y)
    ax.relim()
    ax.autoscale_view()
    plt.draw()

slider_force.on_changed(update)
slider_angle.on_changed(update)
slider_mass.on_changed(update)

# начальная траектория
update(None)

plt.show()
