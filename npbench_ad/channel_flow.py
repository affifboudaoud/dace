import numpy as np
import dace as dc
from dace.autodiff import add_backward_pass

nx, ny, nit = (dc.symbol(s, dc.int64) for s in ('nx', 'ny', 'nit'))


@dc.program
def build_up_b(rho: dc.float64, dt: dc.float64, dx: dc.float64, dy: dc.float64,
               u: dc.float64[ny, nx], v: dc.float64[ny, nx]):
    b = np.zeros_like(u)
    b[1:-1,
      1:-1] = (rho * (1 / dt * ((u[1:-1, 2:] - u[1:-1, 0:-2]) / (2 * dx) +
                                (v[2:, 1:-1] - v[0:-2, 1:-1]) / (2 * dy)) -
                      ((u[1:-1, 2:] - u[1:-1, 0:-2]) / (2 * dx))**2 - 2 *
                      ((u[2:, 1:-1] - u[0:-2, 1:-1]) / (2 * dy) *
                       (v[1:-1, 2:] - v[1:-1, 0:-2]) / (2 * dx)) -
                      ((v[2:, 1:-1] - v[0:-2, 1:-1]) / (2 * dy))**2))

    # Periodic BC Pressure @ x = 2
    b[1:-1, -1] = (rho * (1 / dt * ((u[1:-1, 0] - u[1:-1, -2]) / (2 * dx) +
                                    (v[2:, -1] - v[0:-2, -1]) / (2 * dy)) -
                          ((u[1:-1, 0] - u[1:-1, -2]) / (2 * dx))**2 - 2 *
                          ((u[2:, -1] - u[0:-2, -1]) / (2 * dy) *
                           (v[1:-1, 0] - v[1:-1, -2]) / (2 * dx)) -
                          ((v[2:, -1] - v[0:-2, -1]) / (2 * dy))**2))

    # Periodic BC Pressure @ x = 0
    b[1:-1, 0] = (rho * (1 / dt * ((u[1:-1, 1] - u[1:-1, -1]) / (2 * dx) +
                                   (v[2:, 0] - v[0:-2, 0]) / (2 * dy)) -
                         ((u[1:-1, 1] - u[1:-1, -1]) / (2 * dx))**2 - 2 *
                         ((u[2:, 0] - u[0:-2, 0]) / (2 * dy) *
                          (v[1:-1, 1] - v[1:-1, -1]) /
                          (2 * dx)) - ((v[2:, 0] - v[0:-2, 0]) / (2 * dy))**2))

    return b


@dc.program
def pressure_poisson_periodic(p: dc.float64[ny, nx], dx: dc.float64,
                              dy: dc.float64, b: dc.float64[ny, nx]):
    pn = np.empty_like(p)

    for q in range(nit):
        pn[:] = p.copy()
        p[1:-1, 1:-1] = (((pn[1:-1, 2:] + pn[1:-1, 0:-2]) * dy**2 +
                          (pn[2:, 1:-1] + pn[0:-2, 1:-1]) * dx**2) /
                         (2 * (dx**2 + dy**2)) - dx**2 * dy**2 /
                         (2 * (dx**2 + dy**2)) * b[1:-1, 1:-1])

        # Periodic BC Pressure @ x = 2
        p[1:-1, -1] = (((pn[1:-1, 0] + pn[1:-1, -2]) * dy**2 +
                        (pn[2:, -1] + pn[0:-2, -1]) * dx**2) /
                       (2 * (dx**2 + dy**2)) - dx**2 * dy**2 /
                       (2 * (dx**2 + dy**2)) * b[1:-1, -1])

        # Periodic BC Pressure @ x = 0
        p[1:-1,
          0] = (((pn[1:-1, 1] + pn[1:-1, -1]) * dy**2 +
                 (pn[2:, 0] + pn[0:-2, 0]) * dx**2) / (2 * (dx**2 + dy**2)) -
                dx**2 * dy**2 / (2 * (dx**2 + dy**2)) * b[1:-1, 0])

        # Wall boundary conditions, pressure
        p[-1, :] = p[-2, :]  # dp/dy = 0 at y = 2
        p[0, :] = p[1, :]  # dp/dy = 0 at y = 0


@dc.program
def channel_flow(nit: dc.int64, u: dc.float64[ny, nx], v: dc.float64[ny, nx],
                 dt: dc.float64, dx: dc.float64, dy: dc.float64,
                 p: dc.float64[ny, nx], rho: dc.float64, nu: dc.float64,
                 F: dc.float64, S: dc.float64[1]):
    udiff = 1.0
    stepcount = 0

    while udiff > .001:
        un = u.copy()
        vn = v.copy()

        b = build_up_b(rho, dt, dx, dy, u, v)
        pressure_poisson_periodic(p, dx, dy, b, nit=nit)

        u[1:-1,
          1:-1] = (un[1:-1, 1:-1] - un[1:-1, 1:-1] * dt / dx *
                   (un[1:-1, 1:-1] - un[1:-1, 0:-2]) -
                   vn[1:-1, 1:-1] * dt / dy *
                   (un[1:-1, 1:-1] - un[0:-2, 1:-1]) - dt / (2 * rho * dx) *
                   (p[1:-1, 2:] - p[1:-1, 0:-2]) + nu *
                   (dt / dx**2 *
                    (un[1:-1, 2:] - 2 * un[1:-1, 1:-1] + un[1:-1, 0:-2]) +
                    dt / dy**2 *
                    (un[2:, 1:-1] - 2 * un[1:-1, 1:-1] + un[0:-2, 1:-1])) +
                   F * dt)

        v[1:-1,
          1:-1] = (vn[1:-1, 1:-1] - un[1:-1, 1:-1] * dt / dx *
                   (vn[1:-1, 1:-1] - vn[1:-1, 0:-2]) -
                   vn[1:-1, 1:-1] * dt / dy *
                   (vn[1:-1, 1:-1] - vn[0:-2, 1:-1]) - dt / (2 * rho * dy) *
                   (p[2:, 1:-1] - p[0:-2, 1:-1]) + nu *
                   (dt / dx**2 *
                    (vn[1:-1, 2:] - 2 * vn[1:-1, 1:-1] + vn[1:-1, 0:-2]) +
                    dt / dy**2 *
                    (vn[2:, 1:-1] - 2 * vn[1:-1, 1:-1] + vn[0:-2, 1:-1])))

        # Periodic BC u @ x = 2
        u[1:-1, -1] = (
            un[1:-1, -1] - un[1:-1, -1] * dt / dx *
            (un[1:-1, -1] - un[1:-1, -2]) - vn[1:-1, -1] * dt / dy *
            (un[1:-1, -1] - un[0:-2, -1]) - dt / (2 * rho * dx) *
            (p[1:-1, 0] - p[1:-1, -2]) + nu *
            (dt / dx**2 *
             (un[1:-1, 0] - 2 * un[1:-1, -1] + un[1:-1, -2]) + dt / dy**2 *
             (un[2:, -1] - 2 * un[1:-1, -1] + un[0:-2, -1])) + F * dt)

        # Periodic BC u @ x = 0
        u[1:-1,
          0] = (un[1:-1, 0] - un[1:-1, 0] * dt / dx *
                (un[1:-1, 0] - un[1:-1, -1]) - vn[1:-1, 0] * dt / dy *
                (un[1:-1, 0] - un[0:-2, 0]) - dt / (2 * rho * dx) *
                (p[1:-1, 1] - p[1:-1, -1]) + nu *
                (dt / dx**2 *
                 (un[1:-1, 1] - 2 * un[1:-1, 0] + un[1:-1, -1]) + dt / dy**2 *
                 (un[2:, 0] - 2 * un[1:-1, 0] + un[0:-2, 0])) + F * dt)

        # Periodic BC v @ x = 2
        v[1:-1, -1] = (
            vn[1:-1, -1] - un[1:-1, -1] * dt / dx *
            (vn[1:-1, -1] - vn[1:-1, -2]) - vn[1:-1, -1] * dt / dy *
            (vn[1:-1, -1] - vn[0:-2, -1]) - dt / (2 * rho * dy) *
            (p[2:, -1] - p[0:-2, -1]) + nu *
            (dt / dx**2 *
             (vn[1:-1, 0] - 2 * vn[1:-1, -1] + vn[1:-1, -2]) + dt / dy**2 *
             (vn[2:, -1] - 2 * vn[1:-1, -1] + vn[0:-2, -1])))

        # Periodic BC v @ x = 0
        v[1:-1,
          0] = (vn[1:-1, 0] - un[1:-1, 0] * dt / dx *
                (vn[1:-1, 0] - vn[1:-1, -1]) - vn[1:-1, 0] * dt / dy *
                (vn[1:-1, 0] - vn[0:-2, 0]) - dt / (2 * rho * dy) *
                (p[2:, 0] - p[0:-2, 0]) + nu *
                (dt / dx**2 *
                 (vn[1:-1, 1] - 2 * vn[1:-1, 0] + vn[1:-1, -1]) + dt / dy**2 *
                 (vn[2:, 0] - 2 * vn[1:-1, 0] + vn[0:-2, 0])))

        # Wall BC: u,v = 0 @ y = 0,2
        u[0, :] = 0
        u[-1, :] = 0
        v[0, :] = 0
        v[-1, :] = 0

        udiff = (np.sum(u) - np.sum(un)) / np.sum(u)
        stepcount += 1
    

    @dc.map(_[0:ny, 0:nx])
    def summap(i, j):
        s >> S(1, lambda x, y: x + y)[0]
        z << v[i, j]
        s = z

    return stepcount

    


sdfg = channel_flow.to_sdfg()

sdfg.save("log_sdfgs/channel_flow_forward.sdfg")


add_backward_pass(sdfg=sdfg, inputs=["p"], outputs=["S"])

sdfg.save("log_sdfgs/channel_flow_backward.sdfg")