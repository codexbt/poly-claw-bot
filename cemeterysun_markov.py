#!/usr/bin/env python3

import argparse
import sys
import time
import random
import requests

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# ANSI colors
GREEN = '\033[92m'
RED = '\033[91m'
RESET = '\033[0m'

class MarkovTrader:
    def __init__(self, live=False):
        self.live = live
        self.position = 0  # 0: no position, 1: long, -1: short
        self.balance = 1000.0  # starting balance
        self.states = ['up', 'down', 'sideways']
        self.current_state = 'sideways'
        if HAS_NUMPY:
            # Transition matrix
            self.transition_matrix = np.array([
                [0.5, 0.3, 0.2],  # from up
                [0.2, 0.5, 0.3],  # from down
                [0.3, 0.3, 0.4]   # from sideways
            ])
        else:
            self.transitions = {
                'up': {'up': 0.5, 'down': 0.3, 'sideways': 0.2},
                'down': {'up': 0.2, 'down': 0.5, 'sideways': 0.3},
                'sideways': {'up': 0.3, 'down': 0.3, 'sideways': 0.4}
            }

    def get_next_state(self):
        if HAS_NUMPY:
            probs = self.transition_matrix[self.states.index(self.current_state)]
            next_state = np.random.choice(self.states, p=probs)
        else:
            trans = self.transitions[self.current_state]
            rand = random.random()
            cum = 0
            for state, prob in trans.items():
                cum += prob
                if rand <= cum:
                    next_state = state
                    break
        self.current_state = next_state
        return next_state

    def decide_action(self):
        state = self.get_next_state()
        if state == 'up' and self.position <= 0:
            return 'buy'
        elif state == 'down' and self.position >= 0:
            return 'sell'
        else:
            return 'hold'

    def execute_trade(self, action):
        if action == 'buy' and self.position == 0:
            self.position = 1
            print("Bought")
        elif action == 'sell' and self.position == 0:
            self.position = -1
            print("Sold short")
        elif action == 'hold':
            print("Hold")

    def update_balance(self):
        # Simulate profit/loss
        if self.position == 1:
            profit = random.uniform(-10, 20)
        elif self.position == -1:
            profit = random.uniform(-20, 10)
        else:
            profit = 0
        self.balance += profit
        color = GREEN if profit > 0 else RED
        print(f"{color}Position P/L: {profit:.2f}, Balance: {self.balance:.2f}{RESET}")

    def run(self):
        # Dummy request to use requests library
        try:
            response = requests.get('https://httpbin.org/get', timeout=5)
            if response.status_code == 200:
                print("API check successful")
            else:
                print("API check failed")
        except:
            print("API check skipped")

        for i in range(10):  # simulate 10 steps
            action = self.decide_action()
            self.execute_trade(action)
            self.update_balance()
            time.sleep(1)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--paper', action='store_true')
    args = parser.parse_args()

    if args.live:
        print("Running in live mode")
    elif args.paper:
        print("Running in paper mode")
    else:
        print("Specify --live or --paper")
        sys.exit(1)

    trader = MarkovTrader(live=args.live)
    trader.run()

if __name__ == '__main__':
    main()