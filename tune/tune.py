import random, argparse, json, os
from math import log, isinf
from itertools import chain, product
from numpy import argsort, argmax
from operator import mul
from sklearn import ensemble
import isaac as isc
import optimize, tools, model

def unique(L):
    seen = set()
    seen_add = seen.add
    return [ x for x in L if not (x in seen or seen_add(x))]

def pow2range(a, b):
    return [2**x for x in range(a, b)]


def tune(device, operation, json_path): 
    #List devices
    platforms = isc.get_platforms()
    context = isc.context(device)
    
    #List of size tuples to use
    sizes = {}
    sizes[isc.vaxpy] = [(x,) for x in tools.expspace(1e3, 1e7, 4)]
    sizes[isc.mreduction_rows] = product(pow2range(4,17), pow2range(4,17))
    sizes[isc.mreduction_cols] = isc.mreduction_rows
    sizes[isc.mproduct_nn]     = product(pow2range(5, 10), pow2range(5, 10), pow2range(5, 10))
    sizes[isc.mproduct_nn]	   = [(169, 128, 1728)]    
    sizes[isc.mproduct_tn]     = sizes[isc.mproduct_nn]
    sizes[isc.mproduct_nt]     = sizes[isc.mproduct_nn]
    sizes[isc.mproduct_tt]     = sizes[isc.mproduct_nn]
    sizes = unique(list(sizes[operation]))
    sizes = [x for x in sizes if 1e-4 <= tools.memory_footprint(operation, x) <= 1e-1]


    #Training data
    performance = tools.metric_of(operation)
    profiles = []
    X = []
    Y = []
    for idx, x in enumerate(sizes):
        print x
        nparams = len(profiles)
        tree, operands = tools.tree_of(operation, x, context)
        #Check if the current best prediction is not a local optimum
        if idx==0:
            tune = True
            predicted = None
        else:
            if nparams==1:
                predicted = profiles[0]
            else:
                clf = ensemble.RandomForestRegressor(min(10, idx+1), max_depth=min(10, idx+1)).fit(X, Y)
                #clf, nrmse = model.train(X, Y, profiles)
                predperf = clf.predict(x)[0]
                best = (-predperf).argsort()[:5]
                perf = [performance(x, tools.benchmark(operation, profiles[b], tree)) for b in best]
                predicted = profiles[best[argmax(perf)]]
            tune = not optimize.is_local_optimum(predicted, operation, x, context)     
        #Retune if necessary
        if tune:
            #new = optimize.exhaustive(operation, x, context)
            new = optimize.genetic(operation, x, context, niter=1000, naccept=1000, popsize=20, prior=predicted)[0]
            if new not in profiles:
                profiles.append(new)
                if idx > 0:
                    for xx,yy in zip(X, Y):
                        _tree, _operands = tools.tree_of(operation, xx, context)
                        time = tools.benchmark(operation, new, _tree)
                        perf = performance(xx, time)
                        yy.append(0 if isinf(perf) else perf)
        #Update dataset
        y = []
        fastest = max(predperf) if nparams > 1 else None
        for ip, p in enumerate(profiles):
            perf = 0 if fastest and ip < nparams and predperf[ip]/fastest < .1 else performance(x,tools.benchmark(operation, p, tree))
            y.append(0 if isinf(perf) else perf)
        X.append(x)
        Y.append(y)

    
    #Export to JSON
    if os.path.isfile(json_path):
        json_data = json.load(open(json_path, 'r'))
    else:
        json_data = {}
        json_data["version"] = "1.0"
    operation_name = operation.__name__
    if operation_name not in json_data:
        json_data[operation_name] = {}
    json_data[operation_name]['float32'] = {}
    D = json_data[operation_name]['float32']
    if len(profiles) > 1:
        clf, nrmse = model.train(X, Y, profiles)
        D['predictor'] = [{'children_left': e.tree_.children_left.tolist(),
                            'children_right': e.tree_.children_right.tolist(),
                            'threshold': e.tree_.threshold.astype('float64').tolist(),
                            'feature': e.tree_.feature.astype('float64').tolist(),
                            'value': e.tree_.value[:,:,0].astype('float64').tolist()} for e in clf.estimators_]
    D['profiles'] = [map(int, x) for x in profiles]
    json.dump(json_data, open(json_path,'w'))
    

def parse_arguments():
    platforms = isc.get_platforms()
    devices = [d for platform in platforms for d in platform.get_devices()]
    #Command line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--device", default=0, type=int, help='Device to tune for')
    parser.add_argument("-o", "--operation", type=str, required=True, help='Operation to tune for')
    parser.add_argument("-j", "--json", default='', type=str)
    args = parser.parse_args()
    
    device = devices[int(args.device)]
    print("----------------")
    print("Devices available:")
    print("----------------")
    for (i, d) in enumerate(devices):
        selected = '[' + ('x' if device==d else ' ') + ']'
        print selected , '-',  isc.device_type_to_string(d.type), '-', d.name, 'on', d.platform.name
    print("----------------")
    
    
    operation = {'vaxpy': isc.vaxpy, 'dot': isc.reduction,
                 'maxpy': isc.maxpy, 'gemv_n': isc.mreduction_rows, 'gemv_t': isc.mreduction_cols,
                 'gemm_nn': isc.mproduct_nn, 'gemm_tn': isc.mproduct_tn, 'gemm_nt': isc.mproduct_nt, 'gemm_tt':isc.mproduct_tt}[args.operation]
    if not args.json:
        json = tools.sanitize(device.name) + '.json'
    return (device, operation, json)
        
            
if __name__ == "__main__":
    isc.state.queue_properties = isc.CL_QUEUE_PROFILING_ENABLE
    args = parse_arguments()
    tune(*args)

