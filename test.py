import torch
import torchvision
import paddle

paddle.utils.run_check()

print(torch.__version__)
print(torchvision.__version__)
print(torch.backends.cudnn.version())

if torch.cuda.is_available():
    print("CUDA is available!")
    print("CUDA version:", torch.version.cuda)
    print("Device name:", torch.cuda.get_device_name(0))
else:
    print("CUDA is not available.")