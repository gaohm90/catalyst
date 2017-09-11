from logbook import Logger

from catalyst.api import (
    record,
    order,
    symbol,
    get_open_orders
)
from catalyst.exchange.stats_utils import get_pretty_stats
from catalyst.utils.run_algo import run_algorithm

algo_namespace = 'arbitrage_neo_eth'
log = Logger(algo_namespace)


def initialize(context):
    log.info('initializing arbitrage algorithm')

    context.buying_exchange = context.exchanges['bittrex']
    context.selling_exchange = context.exchanges['bitfinex']

    context.trading_pair_symbol = 'neo_eth'
    context.trading_pairs = dict()
    context.trading_pairs[context.buying_exchange] = \
        symbol(context.trading_pair_symbol, context.buying_exchange.name)
    context.trading_pairs[context.selling_exchange] = \
        symbol(context.trading_pair_symbol, context.selling_exchange.name)

    context.entry_points = [
        dict(gap=0.03, amount=0.05),
        dict(gap=0.04, amount=0.1),
        dict(gap=0.05, amount=0.5),
    ]
    context.exit_points = [
        dict(gap=-0.02, amount=0.5),
    ]

    context.MAX_POSITIONS = 50
    context.SLIPPAGE_ALLOWED = 0.02

    pass


def place_order(context, amount, buying_price, selling_price, action):
    if action == 'enter':
        enter_exchange = context.buying_exchange
        entry_price = buying_price

        exit_exchange = context.selling_exchange
        exit_price = selling_price

    elif action == 'exit':
        enter_exchange = context.selling_exchange
        entry_price = selling_price

        exit_exchange = context.buying_exchange
        exit_price = buying_price

    else:
        raise ValueError('invalid order action')

    base_currency = enter_exchange.base_currency
    base_currency_amount = enter_exchange.portfolio.cash

    exit_balances = exit_exchange.get_balances()
    exit_currency = context.trading_pairs[
        context.selling_exchange].market_currency

    if exit_currency in exit_balances:
        market_currency_amount = exit_balances[exit_currency]
    else:
        log.warn(
            'the selling exchange {exchange_name} does not hold '
            'currency {currency}'.format(
                exchange_name=exit_exchange.name,
                currency=exit_currency
            )
        )
        return

    if base_currency_amount < (amount * entry_price):
        adj_amount = base_currency_amount / entry_price
        log.warn(
            'not enough {base_currency} ({base_currency_amount}) to buy '
            '{amount}, adjusting the amount to {adj_amount}'.format(
                base_currency=base_currency,
                base_currency_amount=base_currency_amount,
                amount=amount,
                adj_amount=adj_amount
            )
        )
        amount = adj_amount
    elif market_currency_amount < amount:
        log.warn(
            'not enough {currency} ({currency_amount}) to sell '
            '{amount}, aborting'.format(
                currency=exit_currency,
                currency_amount=market_currency_amount,
                amount=amount
            )
        )
        return

    adj_buy_price = entry_price * (1 + context.SLIPPAGE_ALLOWED)
    log.info(
        'buying {amount} {trading_pair} on {exchange_name} with price '
        'limit {limit_price}'.format(
            amount=amount,
            trading_pair=context.trading_pair_symbol,
            exchange_name=enter_exchange.name,
            limit_price=adj_buy_price
        )
    )
    order(
        asset=context.trading_pairs[enter_exchange],
        amount=amount,
        limit_price=adj_buy_price
    )

    adj_sell_price = exit_price * (1 - context.SLIPPAGE_ALLOWED)
    log.info(
        'selling {amount} {trading_pair} on {exchange_name} with price '
        'limit {limit_price}'.format(
            amount=-amount,
            trading_pair=context.trading_pair_symbol,
            exchange_name=exit_exchange.name,
            limit_price=adj_sell_price
        )
    )
    order(
        asset=context.trading_pairs[exit_exchange],
        amount=-amount,
        limit_price=adj_sell_price
    )
    pass


def handle_data(context, data):
    log.info('handling bar {}'.format(data.current_dt))

    buying_price = data.current(
        context.trading_pairs[context.buying_exchange], 'price')

    log.info('price on buying exchange {exchange}: {price}'.format(
        exchange=context.buying_exchange.name.upper(),
        price=buying_price,
    ))

    selling_price = data.current(
        context.trading_pairs[context.selling_exchange], 'price')

    log.info('price on selling exchange {exchange}: {price}'.format(
        exchange=context.selling_exchange.name.upper(),
        price=selling_price,
    ))

    # If for example,
    #   selling price = 50
    #   buying price = 25
    #   expected gap = 1

    # If follows that,
    #   selling price - buying price / buying price
    #   50 - 25 / 25 = 1
    gap = (selling_price - buying_price) / buying_price
    log.info(
        'the price gap: {gap} ({gap_percent}%)'.format(
            gap=gap,
            gap_percent=gap * 100
        )
    )
    record(buying_price=buying_price, selling_price=selling_price, gap=gap)

    for exchange in context.trading_pairs:
        asset = context.trading_pairs[exchange]

        orders = get_open_orders(asset)
        if orders:
            log.info(
                'found {order_count} open orders on {exchange_name} '
                'skipping bar until all open orders execute'.format(
                    order_count=len(orders),
                    exchange_name=exchange.name
                )
            )
            return

    # Consider the least ambitious entry point first
    # Override of wider gap is found
    entry_points = sorted(
        context.entry_points,
        key=lambda point: point['gap'],
    )

    buy_amount = None
    for entry_point in entry_points:
        if gap > entry_point['gap']:
            buy_amount = entry_point['amount']

    if buy_amount:
        log.info('found buy trigger for amount: {}'.format(buy_amount))
        place_order(
            context=context,
            amount=buy_amount,
            buying_price=buying_price,
            selling_price=selling_price,
            action='enter'
        )

    else:
        # Consider the narrowest exit gap first
        # Override of wider gap is found
        exit_points = sorted(
            context.exit_points,
            key=lambda point: point['gap'],
            reverse=True
        )

        sell_amount = None
        for exit_point in exit_points:
            if gap < exit_point['gap']:
                sell_amount = exit_point['amount']

        if sell_amount:
            log.info('found sell trigger for amount: {}'.format(sell_amount))
            place_order(
                context=context,
                amount=sell_amount,
                buying_price=buying_price,
                selling_price=selling_price,
                action='exit'
            )


def analyze(context, stats):
    log.info('the daily stats:\n{}'.format(get_pretty_stats(stats)))
    pass


run_algorithm(
    initialize=initialize,
    handle_data=handle_data,
    analyze=analyze,
    exchange_name='bittrex,bitfinex',
    live=True,
    algo_namespace=algo_namespace,
    base_currency='eth',
    live_graph=False
)