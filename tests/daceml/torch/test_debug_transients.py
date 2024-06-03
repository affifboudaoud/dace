import torch
from torch import nn

import dace
from dace.testing import torch_tensors_close


@dace.module(debug_transients=True)
class Module(nn.Module):
    def forward(self, x):
        y = x + 3
        return y * 5


def test_debug_transients():

    module = Module()

    x = torch.rand(5, 5)
    outputs = module(x)
    output, y, y2 = outputs

    torch_tensors_close("output", (x + 3) * 5, output)
    torch_tensors_close("y2", (x + 3) * 5, y2)
    torch_tensors_close("y", x + 3, y)