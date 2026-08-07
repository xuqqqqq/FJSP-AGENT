# A Mathematical Model and a Firefly Algorithm for an Extended Flexible Job Shop Problem with Availability Constraints

Willian Tessaro Lunardi<sup>1(B)</sup> , Luiz Henrique Cherri<sup>2</sup>, and Holger Voos<sup>1</sup>

<sup>1</sup> Interdisciplinary Centre for Security, Reliability and Trust (SnT), University of Luxembourg, 6 rue Coudenhove-Kalergi, 1359 Luxembourg City, Luxembourg

willian.tessarolunardi,holger.voos @uni.lu

2 Institute of Mathematics and Computer Sciences (ICMC), University of S˜ao Paulo, 400 Avenida Trabalhador S˜ao-Carlense, S˜ao Paulo 13566-590, Brazil lhcherri@icmc.usp.br

Abstract. Manufacturing scheduling strategies have historically ignored the availability of the machines. The more realistic the schedule, more accurate the calculations and predictions. Availability of machines will play a crucial role in the Industry 4.0 smart factories. In this paper, a mixed integer linear programming model (MILP) and a discrete firefly algorithm (DFA) are proposed for an extended multi-objective FJSP with availability constraints (FJSP-FCR). Several standard instances of FJSP have been used to evaluate the performance of the model and the algorithm. New FJSP-FCR instances are provided. Comparisons among the proposed methods and other state-of-the-art reported algorithms are also presented. Alongside the proposed MILP model, a Genetic Algorithm is implemented for the experiments with the DFA. Extensive investigations are conducted to test the performance of the proposed model and the DFA. The comparisons between DFA and other recently published algorithms shows that it is a feasible approach for the stated problem.

Keywords: Firefly algorithm Flexible job-shop scheduling Metaheuristics Mixed integer linear programming Availability constraints

## 1 Introduction

The flexible job shop problem (FJSP) is an extension of the job shop problem (JSP) where is assumed that there is often more than one machine that is able to process a particular manufacturing task. The FJSP can be decomposed into two sub-problems: the machine selection problem (MS) and the operations sequencing problem (OS). Most of the FJSP studies have purely focused on assumptions that machines are continuously available. Nevertheless, in a real-world situation, continuous availability of machines is not normally feasible. Machine unavailable periods might be consequent of pre-scheduling, preventive maintenance, shift pattern, or the overlap of two consecutive time horizons in the rolling time horizon planning algorithm.

There are various types of availability constraints in production systems. They can be categorized as and . The unavailable period of a <sup>fixed non-fixed</sup>fixed availability constraint starts at a fixed time point. Unavailable periods can also be categorized as when it allows an operation to be interrupted and resumed, and when it prevents the interruption of any oper-<sup>non-crossable</sup>ation. means that an operation can continue the processing when <sup>Resumable</sup>it is released from an interruption resultant of an unavailable period and <sup>non-</sup>means an operation must be reprocessed fully after interrupted by an <sup>resumable</sup>unavailable period [9].

Most existing literature focuses on the problem of integrating production scheduling with unavailable periods in the context of a single machine, parallel machine and flow shop (especially two-machine problems). The FJSP with nonresumable operations was addressed in [3]. The periods of unavailability are non-crossable, non-fixed and flexible within an end-time window and have to be determined during the scheduling procedure. In [14], a Genetic Algorithm (GA) was proposed to solve the multi-purpose machine (MPM) scheduling problem with fixed non-crossable unavailable periods in a job shop environment with non-resumable operations. A filtered beam search (FBS) [9], was proposed to solve the FJSP with non-fixed and fixed non-crossable unavailable periods and non-resumable operations.

In this paper, we put forward a mixed integer linear model (MILP) and a discrete firefly algorithm (DFA) for solving the FJSP with fixed crossable unavailable periods and resumable operations. In order to evaluate the performance of our methods, as well to be close to situations that may happen in industrial reality, we propose a new set of instances with fixed availability data. In addition, as the FJSP-FCR is an extension of the traditional FJSP. We also used traditional FJSP instances for the computational experiments. These instances include 35 open problems for FJSP. Through experimental studies, the merits of this work are clearly demonstrated.

The remainder of this paper is structured as follows. The problem formulation and the model are presented in Sect. 2. The discrete firefly algorithm and solution representation are discussed in Sect. 3. Numerical results are reported in Sect. 4. Finally, conclusions are presented at the end of this work.

## 2 MILP Model

The formulation of the FJSP-FCR can be given as follows. There is a set of n jobs and a set of m machines. Each job i consists of a sequence of $J _ { i }$ operations. M denotes the set of all machines. Each operation $O _ { i j } ( i = 1 , \dots , n ; j = 1 , \dots , J _ { i } )$ has to be processed on a machine k out of a set of given compatible machines $M _ { i j }$ $( k \in M _ { i j } , M _ { i j } \subseteq M )$ . In this work, we extend the classical FJSP formulation and we consider that operations are resumable and machines are not continuously available. Each machine k has $M _ { k }$ crossable unavailable periods. We denote $U _ { k r }$ as the rth crossable unavailable period on machine $k ,$ with $s u _ { k r }$ and $c u _ { k r }$ being respectively the unavailable period starting and completion time.

The notations used in this paper are summarized below.

<div class="mineru-algorithm" style="white-space: pre-wrap; font-family:monospace;">
Indices

k : index of machines,  $k = 1, \ldots, m$ ;

r : index of unavailabilities,  $r = 1, \ldots, M_k$ ;

i, h : index of jobs, i,  $h = 1, \ldots, n$ ;

j, g : index of operation sequences,  $j, g = 1, \ldots, J_i$ ;

Parameters

 $J_i$  : total number of operation of job i;

 $M_k$  : total number of unavailable periods at machine k;

 $O_{ij}$  : the jth operation of job i;

 $M_{ij}$  : machines able to perform operation  $O_{ij}$ ;

 $p_{ijk}$  : processing time of  $O_{ij}$  on machine k;

 $U_{kr}$  : the rth unavailable period of machine k;

 $su_{kr}$  : starting time of unavailable period  $U_{kr}$ ;

 $cu_{kr}$  : completion time of unavailable period  $U_{kr}$ ;

 $\lambda$  : an weight coefficient;

L : an arbitrary large positive number;

Decision variables

 $C_{max}$  : maximal completion time of the machines;

 $W_{max}$  : maximal workload of the machines ( $\max_{k}\{W_k\}$ );

 $s_{ijk}$  : starting time of operation  $O_{ij}$  on machine k;

 $c_{ijk}$  : completion time of the operation  $O_{ij}$ ;

 $v_{ijk}$  :  $\begin{cases} 1 &amp; \text{if } O_{ij} \text{ is performed on machine } k \\ 0 &amp; \text{otherwise;} \end{cases}$ $u_{ijkr}$  :  $\begin{cases} 1 &amp; \text{if } s_{ijk} &lt; su_{kr} &lt; c_{ijk} \quad \forall i, j, r \forall k \in M_{ij} \\ 0 &amp; \text{otherwise;} \end{cases}$ $y_{ijkr}$  :  $\begin{cases} 1 &amp; \text{if } U_{kr} \text{ precedes operation } O_{ij} \text{ on machine } k \\ 0 &amp; \text{otherwise;} \end{cases}$ $z_{ijhgk}$  :  $\begin{cases} 1 &amp; \text{if } O_{ij} \text{ precedes operation } O_{hg} \text{ on machine } k \\ 0 &amp; \text{otherwise.} \end{cases}$
</div>

The mixed integer programming model for the FJSP-FCR can be given as follows:

$$
\text { minimize } \lambda_ {1} C _ {m a x} + \lambda_ {2} W _ {m a x} + \lambda_ {3} \sum_ {k = 1} ^ {m} \sum_ {i = 1} ^ {n} \sum_ {j = 1} ^ {J _ {i}} p _ {i j k} v _ {i j k}\tag{1}
$$

$$
\mathrm{s.t.} C _ {m a x} \geqslant \sum_ {k \in M _ {i j}} c _ {i j k},
$$

$$
\forall i, j = J _ {i}\tag{2}
$$

$$
W _ {m a x} \geqslant \sum_ {i = 1} ^ {n} \sum_ {j = 1} ^ {J _ {i}} p _ {i j k} v _ {i j k},
$$

$$
\forall k\tag{3}
$$

$$
\begin{array}{l} c _ {i j k} \geqslant s _ {i j k} + p _ {i j k} \\ \quad + \sum_ {\forall r} (c u _ {r k} - s u _ {r k}) u _ {i j k r} \\ \quad - (1 - v _ {i j k}) L, \end{array}
$$

$$
\forall i, j, k \in M _ {i j}\tag{4}
$$

$$
s _ {i j k} \geqslant c _ {h g k} - z _ {i j h g k} L,
$$

$$
\forall i <   h, j, g, k \in M _ {i j} \cap M _ {h g}\tag{5}
$$

$$
s _ {h g k} \geqslant c _ {i j k} - (1 - z _ {i j h g k}) L,
$$

$$
\forall i <   h, j, g, k \in M _ {i j} \cap M _ {h g}\tag{6}
$$

$$
c _ {i j k} \leqslant s u _ {k r} + u _ {i j k r} L + y _ {i j k r} L
$$

$$
\forall i, j, r, k \in M _ {i j}\tag{7}
$$

$$
s _ {i j k} \geqslant c u _ {k r} - (1 - y _ {i j k r}) L,
$$

$$
\forall i, j, r, k \in M _ {i j}\tag{8}
$$

$$
\sum_ {k \in M _ {i j}} s _ {i j k} \geqslant \sum_ {k \in M _ {i j}} c _ {i j - 1 k},
$$

$$
\forall i, j = 2, \dots , J _ {i}\tag{9}
$$

$$
\sum_ {k \in M _ {i j}} v _ {i j k} = 1,
$$

$$
\forall i, j\tag{10}
$$

$$
s _ {i j k} \leqslant v _ {i j k} L,
$$

$$
\forall i, j, k \in M _ {i j}\tag{11}
$$

$$
c _ {i j k} \leqslant v _ {i j k} L,
$$

$$
\forall i, j, k \in M _ {i j}\tag{12}
$$

Objective function (1) ensures the minimization of maximal completion time, maximal workload, and total workload of the machines and is supported by constraints (2) and (3). Constraints (11) and (12) ensures that the start and the completion time of operation on a specific machine is zero if it is not performed on this machine. The duration of the operation, considering its processing time and all the unavailabilities it passes through, is ensured by Constraints (4). Constraints (5) and (6) guarantee that two operations do not overlap on the same machine. Constraints (7) and (8) certify that the operations do not overlap the unavailabilities and, if it occurs, it is accounted to increase the operation time (performed by Constraints (4)). The precedence of each job operations is established by Constraints (9). Constraints (10) states that one machine can be selected from the set of available machines for each operation. The parameter <sup>L</sup>is an upper bound to the maximum processing time and unavailable time and is calculated as $\begin{array} { r } { \sum _ { i } ^ { n } \sum _ { j } ^ { J _ { i } } \operatorname* { m a x } _ { \forall k \in M _ { i j } } p _ { i j k } + \operatorname* { m a x } _ { k = 1 , \ldots , m } \left( \sum _ { r = 1 } ^ { M _ { k } } c u _ { r k } - s u _ { r k } \right) } \end{array}$

## 3 Firefly Algorithm

The firefly algorithm is a nature-inspired meta-heuristic for solving continuous problems and has been motivated by the simulation of the social behavior of fireflies. The two fundamental functions of its flashing lights are to attract mating partners (communication), and to attract potential prey.

<div class="mineru-algorithm" style="white-space: pre-wrap; font-family:monospace;">
Algorithm 1. Firefly Algorithm
1: Objective function $f(x)$, $x = (x_1, \ldots, x_d)^T$
2: Generate initial pop. $P$ of fireflies $x_i (i = 1, 2, \ldots, c)$
3: Light intensity $I_i = f(x_i)$
4: Define light absorption coefficient $\gamma$
5: while ($t &lt; MaxGeneration$) do
6:    for each $x_i \in P$ do
7:    for each $x_j \in P$ do
8:    if ($I_i &lt; I_j$) then Move $x_i$ towards $x_j$ end if
9:    Vary $\beta$ with distance $r$ via $exp[-\gamma r]$
10:    Evaluate solutions and update light intensity
11:    end for $j$
12:    end for $i$
13:    Rank fireflies and find the current global best
14: end while
</div>

In essence, FA uses the three following idealized rules: all fireflies are unisex; attractiveness $\beta$ is proportional to their brightness, in this way for any two flashing fireflies, the less bright one will move towards the brighter one; the brightness of a firefly is afected or determined by the landscape of the objective function. The pseudo code shown in Algorithm 1 summarizes the basic steps of the FA.

## 3.1 Variations of Light Intensity and Attractiveness

The variation of light intensity and formulation of the attractiveness are two important issues. For simplicity, we can always assume the attractiveness of a firefly is determined by its brightness, which in turn is associated with the encoded objective function.

The attractiveness function $\beta ( r )$ can be any monotonically decreasing functions such as the following generalized form

$$
\beta (r) = \beta_ {0} e ^ {- \gamma r ^ {m}}, m \geqslant 1,\tag{13}
$$

where $\beta _ { 0 }$ is the attractiveness at $r = 0$ , and r is the distance between two fireflies. The Eq. (13) can be approximated as

$$
\beta (r) = \frac {\beta_ {0}}{1 + \gamma r ^ {2}}.\tag{14}
$$

The distance between any two fireflies $i$ and $j ,$ at position $x _ { i }$ and $x _ { j }$ , can be defined as a Cartesian distance:

$$
r _ {i j} = \| x _ {i} - x _ {j} \| = \sqrt {\sum_ {k = 1} ^ {d} (x _ {i k} - x _ {j k}) ^ {2}},\tag{15}
$$

where $x _ { i k }$ is the kth component of the spatial cordinate $x _ { i }$ of ith firefly.

<table><tr><td rowspan="2">OS</td><td>2</td><td>1</td><td>3</td><td>2</td><td>3</td><td>1</td><td>1</td><td>2</td></tr><tr><td> $O_{21}$ </td><td> $O_{11}$ </td><td> $O_{31}$ </td><td> $O_{22}$ </td><td> $O_{32}$ </td><td> $O_{12}$ </td><td> $O_{13}$ </td><td> $O_{23}$ </td></tr><tr><td rowspan="2">MS</td><td>2</td><td>4</td><td>3</td><td>1</td><td>3</td><td>4</td><td>2</td><td>1</td></tr><tr><td> $O_{11}$ </td><td> $O_{12}$ </td><td> $O_{13}$ </td><td> $O_{21}$ </td><td> $O_{22}$ </td><td> $O_{23}$ </td><td> $O_{31}$ </td><td> $O_{32}$ </td></tr></table>

Fig. 1. Example of OS string and MS string of a firefly.

The random movement of a firefly i towards another more brighter firefly j is determined by

$$
x _ {i} = x _ {i} + \beta_ {0} e ^ {- \gamma r _ {i j} ^ {2}} (x _ {i} - x _ {j}) + \alpha \epsilon_ {i},\tag{16}
$$

where the second term considers a firefly’s attractiveness, the third term is randomization with α being the randomization parameter, and $\epsilon _ { i }$ is a vector of random numbers drawn from a Gaussian distribution or uniform distribution. For most applications we can take $\beta _ { 0 } = 1 , \mathsf { \alpha } \in [ 0 , 1 ]$ . The parameter γ is crucially important in determining the speed of the convergence and how the FA algorithm behaves. For most applications, it typically varies from 0.001 to 1000. In this implementation of the algorithm, we used $\beta _ { 0 } = 1 . 0 , \alpha \in [ 0 , 1 ]$ and $\gamma = 0 . 1$

## 3.2 Firefly Representation for the FJSP

In our proposed algorithm, each firefly represents an FJSP solution, i.e. operation sequence and machine assignment. The algorithm starts with an initial population of fireflies. Each firefly is attracted by other fireflies to varying degrees, on the basis of the objective value of those solutions and the distance between them, i.e. how diferent they are. The population of fireflies evolves by each firefly randomly (not directly) moving toward the most attractive solution.

The FJSP contains two sub-problems, in this way, our representation contains two strings. The MS string denotes the selected machine for the corresponding operations of each job. The hth part of the MS string can assume any value $k \in M _ { v }$ and represents the assigned machine for operation v.

The OS string represents the order in which the operations will be processed in their respective machines. This representation uses an unpartitioned permutation with $J _ { i }$ repetitions of the job numbers, i.e. each job number appears $J _ { i }$ times in the OS string. By scanning the OS string from left to right, the fth appearance of a job number refers to the fth operation of this job. In this way, any permutation of the OS string can be decoded into a feasible solution and avoid the use of a repair mechanism. When a firefly is decoded, the OS string is translated into a sequence of operation at first. Figure 1 presents an example of OS and the MS strings.

The computation of the makespan can be obtained using graph traversal algorithms, commonly used in temporal planning. During the computation of the makespan for the FJSP-FCR, due to the advent of the unavailable periods, before updating the outcome edges and vertices, is necessary to check whether there is an overlap of the operation with an unavailable period in the machine route. Thus, if the starting of the operation is overlapping the unavailable interval, the starting of the operation must be delayed to the end of the unavailable period; if the starting of the unavailable period is overlapping the operation, the processing time of the operation must be increased by the extension of the unavailable period.

Table 1. Update of the movement of firefly <sup>i</sup> towards a brighter firefly <sup>j</sup>.

<table><tr><td></td><td>MS string</td><td>OS string</td></tr><tr><td>Firefly j</td><td>1 4 1 3 2 2 3 4</td><td>1 2 3 2 1 1 2 3</td></tr><tr><td>Firefly i</td><td>2 4 3 1 3 4 2 1</td><td>2 1 3 2 3 1 1 2</td></tr><tr><td> $H_{ij}$  and  $S_{ij}$ </td><td> $\{(1, 1), (3, 1), (4, 3), (5, 2), (6, 2), (7, 3), (8, 4)\}$ </td><td> $\{(1, 2), (5, 6), (6, 7), (7, 8)\}$ </td></tr><tr><td> $|H_{ij}|$  and  $|S_{ij}|$ </td><td>7</td><td>4</td></tr><tr><td>Attractiveness  $\beta(r)$ </td><td>0.17</td><td>0.38</td></tr><tr><td>rand ∈ [0, 1]</td><td> $\{0.35, 0.1, 0.09, 0.14, 0.33, 0.49, 0.32\}$ </td><td> $\{0.52, 0.05, 0.12, 0.69\}$ </td></tr><tr><td>Movement  $\beta$ -step</td><td> $\{(3, 1), (4, 3), (5, 2)\}$ </td><td> $\{(5, 6), (6, 7)\}$ </td></tr><tr><td>Position after  $\beta$ -step</td><td>2 4 1 3 2 4 2 1</td><td>2 1 3 2 1 1 3 2</td></tr><tr><td>Position after  $\alpha$ -step</td><td>2 4 1 3 2 2 4 1</td><td>2 1 3 2 1 1 2 3</td></tr></table>

## 3.3 Discrete Firefly Algorithm for the FJSP

The FA has been originally developed for solving continuous optimization problems and cannot be directly applied to solve discrete optimization problems. The main challenges for using the FA to solve FJSP are computing the discrete distance between two fireflies, and how they move in the coordination. In this work, the discretization is done for the following issues.

## 3.4 Distance

The discrete distance between two fireflies is defined by the distance between the permutation of its strings. There are two possible ways to measure the distance between two permutations: ( ) Swapping distance $( S _ { i j } )$ , i.e. the number of min-<sup>a</sup>imal required swaps in a permutation i in order to obtain $j ;$ and ( ) Hamming distance $( H _ { i j } )$ <sup>b</sup>, i.e. the number of non-corresponding elements in the sequence of i compared with sequence $j$

The distance between two MS strings can be measured by using Hamming distance. The minimal number of swaps cannot be used for the MS string since two diferent strings can contain diferent elements. Given two MS strings, $M S _ { i } = \{ 2 \ 4 \ 3 \ 1 \ 3 \ 4 \ 2 \ 1 \}$ and $M S _ { j } = \{ 1 4 1 3 2 2 3 4 \}$ , every bit is compared and the number of bits whose are not equal are recorded, the Hamming distance is $H _ { i j } ~ = ~ 7$ . The distance between two OS strings of two fireflies can be measured with the so-called swapping distance. Given two OS strings, $O S _ { i } \ =$ $\{ 2 \ 1 \ 3 \ 2 \ 3 \ 1 \ 1 \ 2 \}$ and $O S _ { j } ~ = ~ \{ 1 ~ 2 ~ 3 ~ 2 ~ 1 ~ 1 ~ 2 ~ 3 \}$ , the swapping distance is $S _ { i j } = 4$

Table 2. The experimental results on Fattahi instances.

<table><tr><td rowspan="2">Instance</td><td rowspan="2">n</td><td rowspan="2">o</td><td rowspan="2">m</td><td colspan="2">OOY</td><td colspan="2">WLH</td><td colspan="2">DFA</td></tr><tr><td> $C_{max}$ </td><td>CPU</td><td> $C_{max}$ </td><td>CPU</td><td> $C_{max}$ </td><td>CPU</td></tr><tr><td>MFJS01</td><td>5</td><td>3</td><td>6</td><td>468</td><td>0.20</td><td>468</td><td>0.21</td><td>468</td><td>0.11</td></tr><tr><td>MFJS02</td><td>5</td><td>3</td><td>7</td><td>446</td><td>0.32</td><td>446</td><td>0.32</td><td>446</td><td>0.18</td></tr><tr><td>MFJS03</td><td>6</td><td>3</td><td>7</td><td>466</td><td>0.90</td><td>466</td><td>0.91</td><td>466</td><td>0.36</td></tr><tr><td>MFJS04</td><td>7</td><td>3</td><td>7</td><td>554</td><td>2.54</td><td>554</td><td>2.56</td><td>554</td><td>1.99</td></tr><tr><td>MFJS05</td><td>7</td><td>3</td><td>7</td><td>514</td><td>1.64</td><td>514</td><td>1.78</td><td>514</td><td>1.28</td></tr><tr><td>MFJS06</td><td>8</td><td>3</td><td>7</td><td>634</td><td>3.80</td><td>634</td><td>3.88</td><td>634</td><td>4.46</td></tr><tr><td>MFJS07</td><td>8</td><td>4</td><td>7</td><td>879</td><td>43.33</td><td>879</td><td>44.54</td><td>879</td><td>9.34</td></tr><tr><td>MFJS08</td><td>9</td><td>4</td><td>8</td><td>884</td><td>977</td><td>884</td><td>1050.55</td><td>884</td><td>15.23</td></tr><tr><td>MFJS09</td><td>11</td><td>4</td><td>8</td><td>[877.9; 1111]20.98%</td><td>3600</td><td>[861; 116]22.85%</td><td>3600</td><td>1055</td><td>31.22</td></tr><tr><td>MFJS10</td><td>12</td><td>4</td><td>8</td><td>[1012; 1208]16.23%</td><td>3600</td><td>[1008.2; 1220]17.36%</td><td>3600</td><td>1196</td><td>39.32</td></tr></table>

## 3.5 Attraction and Movement

In this study we break up the movement given in Eq. (16) into two sub-steps: β-step and α-step. The attraction steps $\beta$ and α are not interchangeable, thereby, β-step must be computed before α-step while finding the new position. Both steps are illustrated in details on Table 1, where the firefly i updates its position towards the a best firefly j. The parameters used in this illustration are as follows: $\beta _ { 0 } = 1 , \gamma = 0 . 1 , \alpha = 1$

Moving Towards Another Firefly: ${ \beta } \mathrm { - } \mathbf { S t e p }$ The $_ { \beta \mathrm { - s t e p } }$ brings the iterated <sup>Moving Towards Another Firefly: -Step.</sup>firefly closer to another firefly. An insertion mechanism and a pair-wise exchange mechanism are used to advance the MS string and OS string of a firefly towards the brighter firefly position. At first, all necessary insertions in the MS string and all pair-wise exchanges in the OS string, to make the elements of the current firefly equal to the best firefly, are computed and store in $H _ { i j }$ and $S _ { i j }$ The Hamming distance and swap distance are respectively defined by $| H _ { i s } |$ and $| S _ { i j } |$ . The $\beta$ probability is computed using Eq. (14). Secondly, it is defined which elements of $H _ { i j }$ and $S _ { i j }$ will be used to change the current solution. A random number rand $\in \ [ 0 , 1 ]$ is generated for each element, and if rand $\leqslant \beta .$ , then the corresponding insertion/pair-wise exchange is performed on the elements of the current firefly.

Table 3. The experimental results (computational time in terms of seconds) on the proposed instances with fixed available periods.

<table><tr><td rowspan="2">Instance</td><td rowspan="2">n</td><td rowspan="2">o</td><td rowspan="2">m</td><td rowspan="2">u</td><td colspan="2">WLH</td><td colspan="2">GA</td><td colspan="2">DFA</td></tr><tr><td> $C_{max}$ </td><td>CPU</td><td> $C_{max}$ </td><td>CPU</td><td> $C_{max}$ </td><td>CPU</td></tr><tr><td>FCR01</td><td>5</td><td>3</td><td>6</td><td>6</td><td>513</td><td>0.20</td><td>513</td><td>3.57</td><td>513</td><td>0.13</td></tr><tr><td>FCR02</td><td>5</td><td>3</td><td>7</td><td>9</td><td>548</td><td>0.56</td><td>552</td><td>7.18</td><td>548</td><td>0.16</td></tr><tr><td>FCR03</td><td>6</td><td>3</td><td>7</td><td>14</td><td>620</td><td>2.50</td><td>620</td><td>5.80</td><td>620</td><td>0.44</td></tr><tr><td>FCR04</td><td>7</td><td>3</td><td>7</td><td>17</td><td>746</td><td>27.46</td><td>748</td><td>5.86</td><td>746</td><td>2.33</td></tr><tr><td>FCR05</td><td>7</td><td>3</td><td>7</td><td>20</td><td>693</td><td>20.94</td><td>709</td><td>11.23</td><td>693</td><td>4.28</td></tr><tr><td>FCR06</td><td>8</td><td>3</td><td>7</td><td>20</td><td>774</td><td>4.83</td><td>777</td><td>11.31</td><td>774</td><td>6.17</td></tr><tr><td>FCR07</td><td>8</td><td>4</td><td>7</td><td>12</td><td>[1000; 1024] 2.34%</td><td>3600</td><td>1044</td><td>28.46</td><td>1024</td><td>10.34</td></tr><tr><td>FCR08</td><td>9</td><td>4</td><td>8</td><td>35</td><td>[1414; 1467] 3.61%</td><td>3600</td><td>1478</td><td>35.22</td><td>1418</td><td>16.78</td></tr><tr><td>FCR09</td><td>11</td><td>4</td><td>8</td><td>49</td><td>[1410.96; 2051] 31.21%</td><td>3600</td><td>1976</td><td>65.65</td><td>1944</td><td>34.70</td></tr><tr><td>FCR10</td><td>12</td><td>4</td><td>8</td><td>52</td><td>[1815.26; 2631] 31.00%</td><td>3600</td><td>2337</td><td>70.64</td><td>2320</td><td>43.01</td></tr></table>

α The α-step is much simpler than the $_ { \beta \mathrm { - s t e p } }$ <sup>Random Movement: -Step.</sup>The random movement of firefly $\alpha ( r a n d - 1 / 2 )$ is approximated as $\alpha ( r a n d _ { i n t } )$ given Eq. 17.

$$
x _ {i} = x _ {i} + \alpha (r a n d _ {i n t}).\tag{17}
$$

It allows us to shift the permutation into one of the neighbouring permutations, by choosing an element position using $\alpha ( r a n d _ { i n t } )$ and swap with another position in the string which also chosen at random, where $r a n d _ { i n t }$ is a positive integer generated between the minimum and maximum number of elements in the string.

## 4 Numerical Results

To solve the MILP models, we used the IBM ILOG CPLEX 12.7 solver with default parameters and a time limit of 3600 seconds. The DFA proposed in this work, and the Genetic Algorithm (GA) proposed in a previous work [7], were coded in C++. The MILP models, the DFA and the GA were run on an Intel Core i7 2.70 GHz, with 8 GB of RAM memory. The best and average results from 50 diferent runs were collected for performance comparison. Observations among the MILP model, the proposed DFA and GA, and other state-of-theart reported algorithms are also provided to determine their performance. To demonstrate the eficiency of the proposed methods, the computational time is further compared.

The instances used in the experiments can be characterized by number of jobs n, number of machines m, number of operations o, and number of unavailable periods u. The DFA parameters consist of the population size $P ,$ a maximum number of generations $G ,$ , firefly’s attractiveness $\beta _ { 0 }$ , light absorption $\gamma ,$ , and randomization α. We kept fixed the following parameters: $\beta _ { 0 } = 1 . 0 , \alpha \in [ 0 , 1 ]$ , and $\gamma = 0 . 1$ . The variation of P and G was based on the size of each instance. We used $P = 1 2 5$ and $G = 1 0 0$ for small instances, i.e. less or equal to 6 jobs and 5 machines; $P = 2 5 0$ and $G = 2 0 0$ for medium instances i.e. less or equal to 10 jobs and 8 machines; $P = 5 0 0$ and $G = 3 0 0$ for instances that does not belong to another group.

Table 4. Comparison of the DFA with other algorithms on Brandimarte instances.

<table><tr><td rowspan="2">Instance</td><td rowspan="2">n</td><td rowspan="2">m</td><td rowspan="2">o</td><td colspan="2">TABC</td><td colspan="2">MA</td><td colspan="2">DFA</td></tr><tr><td> $C_{max}$ </td><td>CPU</td><td> $C_{max}$ </td><td>CPU</td><td> $C_{max}$ </td><td>CPU</td></tr><tr><td>Mk01</td><td>10</td><td>6</td><td>7</td><td>40</td><td>3</td><td>40</td><td>20</td><td>40</td><td>5</td></tr><tr><td>Mk02</td><td>10</td><td>6</td><td>7</td><td>26</td><td>3</td><td>26</td><td>28</td><td>26</td><td>16</td></tr><tr><td>Mk03</td><td>15</td><td>8</td><td>10</td><td>204</td><td>1</td><td>204</td><td>53</td><td>204</td><td>3</td></tr><tr><td>Mk04</td><td>15</td><td>8</td><td>10</td><td>60</td><td>66</td><td>60</td><td>30</td><td>60</td><td>11</td></tr><tr><td>Mk05</td><td>15</td><td>4</td><td>10</td><td>173</td><td>78</td><td>172</td><td>36</td><td>172</td><td>19</td></tr><tr><td>Mk06</td><td>10</td><td>15</td><td>15</td><td>60</td><td>173</td><td>59</td><td>80</td><td>59</td><td>63</td></tr><tr><td>Mk07</td><td>20</td><td>5</td><td>5</td><td>139</td><td>66</td><td>139</td><td>37</td><td>139</td><td>43</td></tr><tr><td>Mk08</td><td>20</td><td>10</td><td>15</td><td>523</td><td>2</td><td>523</td><td>77</td><td>523</td><td>4</td></tr><tr><td>Mk09</td><td>20</td><td>10</td><td>15</td><td>307</td><td>304</td><td>307</td><td>75</td><td>307</td><td>34</td></tr><tr><td>Mk10</td><td>20</td><td>15</td><td>15</td><td>202</td><td>418</td><td>202</td><td>90</td><td>202</td><td>94</td></tr></table>

## 4.1 Fattahi Instances

We compare the proposed model and the DFA experimentally with [8] (OOY), a concise MILP model for the FJSP and has proven to be efective when compared to other state-of-the-art MILP models, as shown in [2]. Both models were implemented in the same platform and experiments were conducted in the same computer, mentioned in Sect. 4. The weight coeficients employed in this experiment are: $\lambda _ { 1 } = 1 . 0 , \lambda _ { 2 } = 0 . 0$ , and $\lambda _ { 3 } = 0 . 0$ . Table 2 shows the numerical results of the experiments involving the Fattahi instances.

The proposed model contains additional constraints (compared to OOY model) to address the FJSP-FCR. Even with the additional constraints to address the available periods, our model can achieve similar results solving the standard FJSP. CPLEX found the optimal solution for the instances MFJS01-08. The DFA obtained the best solutions for all instances.

## 4.2 FJSP-FCR Instances

Due to the lack of literature, in order to compare the DFA with other algorithms using the FJSP-FCR instances, we implemented a GA for this experiment. The results of this experiment are presented in the Table 3. This set of instances can be obtained in JSON format through the following URL: https://github.com/ snt-robotics/fjsp fcr.

Table 5. The experimental results on Kacem instances of a multi-objective optimization experiment.

<table><tr><td rowspan="2">Algorithm</td><td colspan="4"> $4 \times 5$ </td><td colspan="4"> $8 \times 8$ </td><td colspan="4"> $10 \times 7$ </td><td colspan="4"> $10 \times 10$ </td><td colspan="4"> $15 \times 10$ </td></tr><tr><td> $f_1$ </td><td> $f_2$ </td><td> $f_3$ </td><td>CPU</td><td> $f_1$ </td><td> $f_2$ </td><td> $f_3$ </td><td>CPU</td><td> $f_1$ </td><td> $f_2$ </td><td> $f_3$ </td><td>CPU</td><td> $f_1$ </td><td> $f_2$ </td><td> $f_3$ </td><td>CPU</td><td> $f_1$ </td><td> $f_2$ </td><td> $f_3$ </td><td>CPU</td></tr><tr><td>AL + CGA</td><td>16</td><td>10</td><td>34</td><td>-</td><td>15</td><td>13</td><td>79</td><td>-</td><td></td><td>-</td><td></td><td>-</td><td>7</td><td>5</td><td>45</td><td>-</td><td>23</td><td>11</td><td>93</td><td>-</td></tr><tr><td>PSO + SA</td><td></td><td>-</td><td></td><td>-</td><td>15</td><td>12</td><td>75</td><td>-</td><td></td><td>-</td><td></td><td>-</td><td>7</td><td>6</td><td>44</td><td>-</td><td>12</td><td>11</td><td>91</td><td>-</td></tr><tr><td>AIA</td><td></td><td>-</td><td></td><td>-</td><td>14</td><td>12</td><td>77</td><td>0.76</td><td></td><td>-</td><td></td><td>-</td><td>7</td><td>5</td><td>43</td><td>8.97</td><td>11</td><td>11</td><td>93</td><td>109.22</td></tr><tr><td>P-DABC</td><td>11</td><td>10</td><td>32</td><td>-</td><td>14</td><td>12</td><td>77</td><td>-</td><td>12</td><td>11</td><td>61</td><td>-</td><td>8</td><td>7</td><td>41</td><td>-</td><td>12</td><td>11</td><td>91</td><td>-</td></tr><tr><td>SMF</td><td>12</td><td>8</td><td>32</td><td>2.6</td><td>14</td><td>12</td><td>77</td><td>39.5</td><td>11</td><td>10</td><td>62</td><td>109.5</td><td>7</td><td>6</td><td>42</td><td>39.1</td><td>11</td><td>10</td><td>93</td><td>864.6</td></tr><tr><td>PSO + TS</td><td>12</td><td>8</td><td>32</td><td>0.34</td><td>14</td><td>12</td><td>77</td><td>1.67</td><td></td><td>-</td><td></td><td>-</td><td>7</td><td>6</td><td>43</td><td>2.05</td><td>11</td><td>11</td><td>93</td><td>10.88</td></tr><tr><td>WLH</td><td>12</td><td>8</td><td>32</td><td>0.06</td><td>14</td><td>12</td><td>77</td><td>0.54</td><td>11</td><td>10</td><td>62</td><td>0.36</td><td>7</td><td>5</td><td>43</td><td>1.07</td><td>11</td><td>11</td><td>93</td><td>211.74</td></tr><tr><td>GA</td><td>11</td><td>10</td><td>32</td><td>0.29</td><td>14</td><td>12</td><td>77</td><td>0.90</td><td>11</td><td>10</td><td>62</td><td>1.57</td><td>7</td><td>6</td><td>42</td><td>2.02</td><td>12</td><td>12</td><td>93</td><td>18.76</td></tr><tr><td>DFA</td><td>12</td><td>8</td><td>32</td><td>0.11</td><td>14</td><td>12</td><td>77</td><td>0.64</td><td>11</td><td>10</td><td>62</td><td>0.84</td><td>7</td><td>5</td><td>43</td><td>1.27</td><td>11</td><td>11</td><td>93</td><td>6.86</td></tr></table>

<sup>n m</sup> total number of jobs and machines. equals not available. $\overline { { f _ { 1 } , f _ { 2 } } }$ and $f _ { 3 }$ are respectively the $C _ { m a x }$ , $W _ { m a x }$ , and total workload of the machines.

In this experiment, we can see that the DFA achieve better results, and is more eficient and efective than the GA. The MILP model has found best solutions for the FCR01,. . . , FCR06, and for the other instances, bounds were provided.

## 4.3 Brandimarte Instances

To better demonstrate the efectiveness of the DFA we compare results with other state-of-the-art algorithms for the FJSP using the Brandimate instances. We compare the DFA with an artificial bee colony algorithm (TABC) [4] and a memetic algorithm (MA) [12]. The TABC was implemented on an Intel 2.4 GHz Core 2 Duo processor with 4.0 GB of RAM memory in C++. The MA was implemented on an Intel Core i7-3520M 2.9 GHz processor with 8.0 GB of RAM memory in Java. The weight coeficients employed in this experiment are: $\lambda _ { 1 } = 1 . 0 , \lambda _ { 2 } = 0 . 0$ , and $\lambda _ { 3 } = 0 . 0$ . Table 4 shows the comparison on the 10 Brandimarte instances. In this experiment, we can see that the DFA can achieve similar results to state-of-the-art algorithms.

## 4.4 Kacem Instances

Kacem et al. proposed five multi-objective FJSP instances. Using these instances our MILP model (WLH), and the DFA are compared with the hybrid particle swarm optimization and tabu search (PSO + TS) [13], implemented on a Pentium IV 1.8 GHz in C++; the discrete artificial bee colony (DABC) [6], implemented on a Pentium IV 1.8 GHz with MB of RAM memory in C++; the artificial immune algorithm (AIA) [1] implemented on a 2.0 GHz processor with 256 MB of RAM memory in C++; the simulation modeling (SMF) [11], implemented on a Pentium IV 2.4 GHz personal with 512 MB RAM memory in Matlab; the hybrid evolutionary and fuzzy logic (AL + CGA) [5]; the GA proposed in [7], and the hybrid particle swarm optimization and simulating annealing $\mathrm { ( P S O + S A ) }$ [10]. The weight coeficients used in this experiment are: $\lambda _ { 1 } = 0 . 5 , \lambda _ { 2 } = 0 . 3$ , and $\lambda _ { 3 } = 0 . 2$ . Table 5 shows the comparison of the results on the five Kacem instances.

## 5 Conclusion

Planning and scheduling with machine availability constraint become increasingly more important as a better understanding of their importance in various applications. We put forward a new MILP model and an FA for the FJSP-FCR. New instances are provided. We further presented computational experiments on classical instances in order to provide comparisons with other state-of-the-art algorithms. The numerical results make clear that the MILP model is important for comparisons with non-exact methods, providing good bounds to many small and medium size instances. The experiments among the DFA and others recently published algorithms shows that it is a feasible approach for the considered problem.

## References

1. Bagheri, A., Zandieh, M., Mahdavi, I., Yazdani, M.: An artificial immune algorithm for the flexible job-shop scheduling problem. Future Gener. Comput. Syst. 26(4), 533–541 (2010)

2. Demir, Y., <sup>˙</sup>I¸sleyen, S.K.: Evaluation of mathematical models for flexible job-shop scheduling problems. Appl. Math. Model. 37(3), 977–988 (2013)

3. Gao, J., Gen, M., Sun, L.: Scheduling jobs and maintenances in flexible job shop with a hybrid genetic algorithm. J. Intell. Manuf. 17(4), 493–507 (2006)

4. Gao, K.Z., Suganthan, P.N., Chua, T.J., Chong, C.S., Cai, T.X., Pan, Q.K.: A two-stage artificial bee colony algorithm scheduling flexible job-shop scheduling problem with new job insertion. Expert Syst. Appl. 42(21), 7652–7663 (2015)

5. Kacem, I., Hammadi, S., Borne, P.: Pareto-optimality approach for flexible jobshop scheduling problems: hybridization of evolutionary algorithms and fuzzy logic. Math. Comput. Simul. 60(3), 245–276 (2002)

6. Li, J.Q., Pan, Q.K., Gao, K.Z.: Pareto-based discrete artificial bee colony algorithm for multi-objective flexible job shop scheduling problems. Int. J. Adv. Manuf. Technol. 55(9), 1159–1169 (2011)

7. Lunardi, W.T., Voos, H.: Comparative study of genetic and discrete firefly algorithm for combinatorial optimization. In: 33rd ACM/SIGAPP Symposium on Applied Computing, Pau, France, 9–13 April 2018 (2018)

8. Ozg¨<sup>¨</sup> uven, C., Ozbakır, L., Yavuz, Y.: Mathematical models for job-shop scheduling<sup>¨</sup> problems with routing and process plan flexibility. Appl. Math. Model. 34(6), 1539– 1548 (2010)

9. Wang, S., Yu, J.: An efective heuristic for flexible job-shop scheduling problem with maintenance activities. Comput. Ind. Eng. 59(3), 436–447 (2010)

10. Xia, W., Wu, Z.: An efective hybrid optimization approach for multi-objective flexible job-shop scheduling problems. Comput. Ind. Eng. 48(2), 409–425 (2005)

11. Xing, L.N., Chen, Y.W., Yang, K.W.: Multi-objective flexible job shop schedule: design and evaluation by simulation modeling. Appl. Soft Comput. 9(1), 362–376 (2009)

12. Yuan, Y., Xu, H.: Multiobjective flexible job shop scheduling using memetic algorithms. IEEE Trans. Autom. Sci. Eng. 12(1), 336–353 (2015)

13. Zhang, G., Shao, X., Li, P., Gao, L.: An efective hybrid particle swarm optimization algorithm for multi-objective flexible job-shop scheduling problem. Comput. Ind. Eng. 56(4), 1309–1318 (2009)

14. Zribi, N., El Kamel, A., Borne, P.: Minimizing the makespan for the MPM job-shop with availability constraints. Int. J. Prod. Econ. 112(1), 151–160 (2008)