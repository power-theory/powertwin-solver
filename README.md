# PowerTwin Solver
This script performs energy modeling for buildings using combinations of relevant machine learning algorithms. It takes in cleaned CSV files of building energy data and produces trained models for energy usage prediction.

## Docker Usage (.)
Build the Docker images using the provided bash script:
```shell
bash run_docker.sh
```

## Setup & Dependencies (./flask_app)
- Conda
    - https://conda.io/projects/conda/en/latest/user-guide/install/index.html
- Tensorflow (Steps Down Below)
    - https://www.tensorflow.org/install/pip#linux

```bash
conda create -n tf-gpu --yes python==3.9
conda activate tf-gpu
conda update --yes --all
conda install --yes -c conda-forge prophet
conda install --yes numpy pandas matplotlib flask

pip3 install --upgrade pip
python3 -m pip install --upgrade setuptools
pip3 install -r requirements.txt
```

Linux/WSL2:
```bash
conda install --yes -c conda-forge cudatoolkit=11.8.0
python3 -m pip install nvidia-cudnn-cu11==8.6.0.163 tensorflow==2.12.*
mkdir -p $CONDA_PREFIX/etc/conda/activate.d
echo 'CUDNN_PATH=$(dirname $(python -c "import nvidia.cudnn;print(nvidia.cudnn.__file__)"))' >> $CONDA_PREFIX/etc/conda/activate.d/env_vars.sh
echo 'export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:$CONDA_PREFIX/lib/:$CUDNN_PATH/lib' >> $CONDA_PREFIX/etc/conda/activate.d/env_vars.sh
source $CONDA_PREFIX/etc/conda/activate.d/env_vars.sh
# Verify install:
python3 -c "import tensorflow as tf; print(tf.config.list_physical_devices('GPU'))"
```

MacOS:
```bash
# There is currently no official GPU support for MacOS.
python3 -m pip install tensorflow
# Verify install:
python3 -c "import tensorflow as tf; print(tf.reduce_sum(tf.random.normal([1000, 1000])))"
```

## Usage
1. Place preprocessed CSV files in a local data folder and call it data.csv (e.g., `./data/data.csv`).
2. Create/assign an explicit 'bldgname' column.
3. Create/assign an explicit 'ts' column.
4. Verify `config.py` to ensure it has the correct configurations for your project.
    a. Make sure 'save_preprocessed_files' is set to True for first time preprocessing.
5. Run the script with the following command:

- To run Flask App
```shell
python3 -m flask run --host=0.0.0.0 --port=8000
```

- To run inside terminal
```shell
python3 main.py
```