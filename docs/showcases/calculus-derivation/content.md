$$\begin{aligned}=limx\rightarrow0\frac{(1+\int_0^x e^{t^2}dt)sinx-e^x+1}{x^2}\\=limx\rightarrow0(\frac{sinx-x}{x^2}+\frac{\int_0^x e^{t^2}dt·sinx-e^x+1+x}{x^2})\\=limx\rightarrow0\frac{\int_0^x e^{t^2}dt·sinx-e^x+1+x}{x^2}\\=limx\rightarrow0\frac{sinx}x·\frac{\int_0^x e^{t^2}dt}x-limx\rightarrow0\frac{e^x-1-x}{x^2}\\=limx\rightarrow0e^{x^2}-limx\rightarrow0\frac{e^x-1}{2x}=1-\frac12=\frac12.\end{aligned}$$

方法二

$$limx\rightarrow0(\frac{1+\int_0^x e^{t^2}dt}{e^x-1}-\frac1{sinx})=limx\rightarrow0(\frac{\int_0^x e^{t^2}dt}{e^x-1}+\frac1{e^x-1}-\frac1{sinx})$$

由 $limx\rightarrow0\frac{\int_0^x e^{t^2}dt}{e^x-1}=limx\rightarrow0\frac{e^{x^2}}{e^x}=1$

$$\begin{aligned}limx\rightarrow0(\frac1{e^x-1}-\frac1{sinx})=limx\rightarrow0\frac{sinx-e^x+1}{(e^x-1)sinx}=limx\rightarrow0\frac{sinx-e^x+1}{x^2}\\=\frac12limx\rightarrow0\frac{cosx-e^x}x=\frac12limx\rightarrow0(-sinx-e^x)=-\frac12,\end{aligned}$$

$$limx\rightarrow0(\frac{1+\int_0^x e^{t^2}dt}{e^x-1}-\frac1{sinx})=1-\frac12=\frac12.$$

方法三

由泰勒公式得  $e^{t^2}=1+t^2+o(t^2)$

从而 $\int_0^x e^{t^2}dt=x+\frac{x^3}3+o(x^3)$，于是有

$$\begin{aligned}limx\rightarrow0(\frac{1+\int_0^x e^{t^2}dt}{e^x-1}-\frac1{sinx})=limx\rightarrow0(\frac{1+x+\frac{x^3}3+o(x^3)}{e^x-1}-\frac1{sinx})=limx\rightarrow0(\frac{1+x}{e^x-1}-\frac1{sinx})\\=limx\rightarrow0\fracx{e^x-1}+limx\rightarrow0(\frac1{e^x-1}-\frac1{sinx})\\=1+limx\rightarrow0\frac{sinx-e^x+1}{(e^x-1)sinx}=1+limx\rightarrow0\frac{sinx-e^x+1}{x^2}\\=1+limx\rightarrow0\frac{cosx-e^x}{2x}=1+limx\rightarrow0\frac{-sinx-e^x}2=\frac12.\end{aligned}$$
