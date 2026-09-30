EXAMPLES = [
 {"id":"newton","title":"Newton 法求根","query":"使用 Newton 法求解 cos(x) - x = 0，初值 0.5，并显示迭代和残差","expected_tool":"find_root","method":"Newton 法","verification":"代回方程检查残差","discussion":"初值如何影响收敛？"},
 {"id":"spline","title":"三次样条插值","query":"对数据 x=[0,1,2,3,4], y=[0,0.8,0.9,0.1,-0.8] 进行三次样条插值并画图","expected_tool":"interpolate_data","method":"三次样条","verification":"检查所有插值节点误差","discussion":"为何高次全局多项式可能振荡？"},
 {"id":"fit","title":"二次多项式拟合","query":"对 x=[0,1,2,3,4,5], y=[1.1,2.8,7.2,12.9,21.2,30.8] 做二次多项式拟合并显示 RMSE","expected_tool":"fit_curve","method":"最小二乘","verification":"RMSE、最大残差和 R²","discussion":"残差中能看出模型偏差吗？"},
 {"id":"symbolic","title":"符号方程","query":"用 SymPy 求解 x^2 - 2 = 0 并代回验证","expected_tool":"solve_symbolic_equation","method":"符号求解","verification":"逐个解代回原方程","discussion":"解析解和浮点近似有何区别？"},
 {"id":"derivative","title":"导数交叉验证","query":"对 sin(x)*exp(x) 符号求导，并与有限差分比较","expected_tool":"differentiate_expression","method":"符号求导","verification":"多点有限差分交叉检查","discussion":"步长太大或太小会发生什么？"},
 {"id":"ode","title":"初值 ODE","query":"求解初值问题 y' = y - t^2 + 1, y(0)=0.5, t 从 0 到 2，并检查残差","expected_tool":"solve_ode","method":"RK45","verification":"初值及离散方程残差","discussion":"容差如何影响步数和误差？"},
 {"id":"retry","title":"验证失败与修正","query":"演示验证失败重试：用很低精度求 cos(x)-x=0，再自动提高精度","expected_tool":"find_root","method":"Newton 法逐步提高精度","verification":"第一次强制失败，修正后检查残差","discussion":"验证为何应独立于计算过程？"},
]

