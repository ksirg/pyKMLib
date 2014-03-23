# -*- coding: utf-8 -*-
"""
Created on Mon Dec 30 13:18:51 2013

@author: Krszysztof Sopyła
@email: krzysztofsopyla@gmail.com
@githubuser: ksirg
@license: MIT
"""


"""
Mainly it demostrates the usage of pycuda.

"""


import pycuda.driver as cuda
import pycuda.autoinit
from pycuda.compiler import SourceModule

import numpy as np

from sklearn import datasets
    

import sys
sys.path.append("../pyKMLib/")
import SparseFormats as spf
import Kernels as ker


#load and reorganize the dataset

#X, Y = datasets.load_svmlight_file('Data/heart_scale')
X, Y = datasets.load_svmlight_file('Data/toy_2d_16.train')
#X, Y = datasets.load_svmlight_file('Data/w8a')

#reorder the dataset and compute class statistics
cls, idx_cls = np.unique(Y, return_inverse=True)
#contains mapped class [0,nr_cls-1]
nr_cls = cls.shape[0] 
new_classes = np.arange(0,nr_cls)
y_map = new_classes[idx_cls]
#reorder the dataset, group class together
order =np.argsort(a=y_map,kind='mergesort')
X = X[order]
Y = Y[order]
count_cls=np.bincount(y_map)
start_cls = count_cls.cumsum()
start_cls=np.insert(start_cls,0,0)

#---------------------


X=X.astype(np.float32)
Y=Y.astype(np.float32)

num_el,dim = X.shape
gamma = 0.5
threadsPerRow = 1
prefetch=2



rbf = ker.RBF()
rbf.gamma=gamma

rbf.init(X,Y)

i=0
j=2
vecI = X[i,:].toarray()
vecJ = X[j,:].toarray()
ki =Y[i]*Y* rbf.K_vec(vecI).flatten()
kj =Y[j]*Y*rbf.K_vec(vecJ).flatten()

kij= np.array( [ki,kj]).flatten()


##----------------------------------------------
# Ellpakc gpu kernel

v,c,r=spf.csr2ellpack(X,align=prefetch)

sd=rbf.Diag
self_dot = rbf.Xsquare
results = np.zeros(2*num_el,dtype=np.float32)

kernel_file = "ellpackKernel.cu"

with open (kernel_file,"r") as CudaFile:
    data = CudaFile.read();

#copy memory to device
g_val = cuda.to_device(v)
g_col = cuda.to_device(c)
g_r   = cuda.to_device(r)
g_self = cuda.to_device(self_dot)
g_y    = cuda.to_device(Y)
g_out = cuda.to_device(results)


#compile module
#module = SourceModule(data,cache_dir='./nvcc_cache',keep=True,no_extern_c=True)

module = SourceModule(data,keep=True,no_extern_c=True)

#get module function
func = module.get_function('rbfEllpackILPcol2')

#get module texture
vecI_tex=module.get_texref('VecI_TexRef')
vecJ_tex=module.get_texref('VecJ_TexRef')

#copy data to tex ref

g_vecI = cuda.to_device(vecI)
vecI_tex.set_address(g_vecI,vecI.nbytes)

g_vecJ = cuda.to_device(vecJ)
vecJ_tex.set_address(g_vecJ,vecJ.nbytes)

texList=[vecI_tex,vecJ_tex]

tpb=128#rozmiar bloku, wielokrotnosc 2

#liczba blokow 
bpg =int( np.ceil( (threadsPerRow*num_el+0.0)/tpb ))

g_num_el = np.int32(num_el)
g_i = np.int32(i)
g_j = np.int32(j)
g_gamma = np.float32(gamma)
func(g_val,g_col,g_r,g_self,g_y,g_out,g_num_el,g_i,g_j,g_gamma,block=(tpb,1,1),grid=(bpg,1),texrefs=texList)


cuda.memcpy_dtoh(results,g_out)

print "Error Ellpack",np.square(results-kij).sum()


##------------------------------------------
# SERTILP gpu kernel


sliceSize=8
threadsPerRow=2
prefetch=2
minAlign=8
v,c,r,ss=spf.csr2sertilp(X,
                         threadsPerRow=threadsPerRow, 
                         prefetch=prefetch, 
                         sliceSize=sliceSize,
                         minAlign=minAlign)

sd=rbf.Diag
self_dot = rbf.Xsquare
results = np.zeros(2*num_el,dtype=np.float32)

kernel_file = "sertilpMulti2Col.cu"

with open (kernel_file,"r") as CudaFile:
    data = CudaFile.read();
#compile module
#module = SourceModule(data,cache_dir='./nvcc_cache',keep=True,no_extern_c=True)
module = SourceModule(data,keep=True,no_extern_c=True)
#get module function
func = module.get_function('rbfSERTILP2multi')

tpb=128#rozmiar bloku, wielokrotnosc 2
#liczba blokow 
bpg =int( np.ceil( (threadsPerRow*num_el+0.0)/tpb ))

#get module texture
vecI_tex=module.get_texref('VecI_TexRef')
vecJ_tex=module.get_texref('VecJ_TexRef')

#copy data to tex ref
g_vecI = cuda.to_device(vecI)
vecI_tex.set_address(g_vecI,vecI.nbytes)
g_vecJ = cuda.to_device(vecJ)
vecJ_tex.set_address(g_vecJ,vecJ.nbytes)

texList=[vecI_tex,vecJ_tex]


#copy memory to device
g_val = cuda.to_device(v)
g_col = cuda.to_device(c)
g_r   = cuda.to_device(r)
g_slice = cuda.to_device(ss)
g_self = cuda.to_device(self_dot)
g_y    = cuda.to_device(Y)
g_out = cuda.to_device(results)

g_num_el = np.int32(num_el)

align = np.ceil( 1.0*sliceSize*threadsPerRow/minAlign)*minAlign
g_align = np.int32(align)
g_i = np.int32(i)
g_j = np.int32(j)
g_i_ds= np.int32(i)
g_j_ds= np.int32(j)



warp=32
align_cls1_n =  cls1_n+(warp-cls1_n%warp)%warp
align_cls2_n =  cls2_n+(warp-cls2_n%warp)%warp        
g_cls1N_aligned = np.int32(align_cls1_n)

#gamma copy to constant memory
(g_gamma,gsize)=module.get_global('GAMMA')       
cuda.memcpy_htod(g_gamma, np.float32(gamma) )



g_cls_start = cuda.to_device(np.array(0))
g_cls_count = cuda.to_device(np.array(0))
g_cls = cuda.to_device(np.array(0))


func(g_val,g_col,g_r,g_self,g_y,g_out,g_num_el,g_i,g_j,g_gamma,block=(tpb,1,1),grid=(bpg,1),texrefs=texList)


cuda.memcpy_dtoh(results,g_out)

print "Error ",np.square(results-kij).sum()


