#!/bin/bash
#SBATCH --job-name=cfd_1842
#SBATCH --account=m4471
#SBATCH --partition=compute
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=32
#SBATCH --mem=8G
#SBATCH --time=02:00:00
#SBATCH --output=/workspace/logs/job_%j.out
#SBATCH --error=/workspace/logs/job_%j.err

module load openmpi/4.1.6
module load hdf5/1.14.3

cd /workspace/projects/project_a/run_1842
srun cfd-solver --params params.json --mesh inputs/mesh.dat
