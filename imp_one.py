import sys
m = sys.argv[1]
try:
    __import__(m)
    print('OK', m)
except Exception as e:
    print('FAIL', m, repr(e))
