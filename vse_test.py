import sys
import unittest
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from typing import List, Optional, Dict, Callable


# ==========================================
# 1. УТИЛИТЫ
# ==========================================
def round_money(value: Decimal) -> Decimal:
    """Банковское округление до копеек (half to even)."""
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)


# ==========================================
# 2. МОДЕЛИ ДАННЫХ (строго по заданию)
# ==========================================
@dataclass
class Product:
    id: str
    name: str
    base_price: Decimal
    category: str


@dataclass
class CartItem:
    product: Product
    quantity: int


@dataclass
class Customer:
    id: str
    last_purchase_date: Optional[date]
    is_first_order: bool


@dataclass
class Order:
    id: str
    customer: Customer
    items: List[CartItem]
    promo_code: Optional[str]
    created_at: date
    delivery_cost: Decimal


@dataclass
class AppliedDiscount:
    name: str
    amount: Decimal
    reason: str


@dataclass
class PricingResult:
    base_total: Decimal
    applied_discounts: List[AppliedDiscount]
    final_total: Decimal


@dataclass
class DiscountContext:
    order: Order
    subtotal: Decimal
    delivery: Decimal
    applied_discounts: List[AppliedDiscount]
    promo_used: bool = False


# ==========================================
# 3. АБСТРАКЦИЯ И СТРАТЕГИИ (Полиморфизм)
# ==========================================
class BaseDiscount:
    """Базовый класс для всех скидок. Движок работает только с ним."""
    name: str = ""
    stage: str = ""  # 'three_for_two', 'percent', 'fixed', 'delivery'
    is_promo: bool = False
    is_loyalty: bool = False
    is_first_order: bool = False

    def apply(self, ctx: DiscountContext) -> Optional[AppliedDiscount]:
        raise NotImplementedError("Метод apply должен быть переопределен")

    def get_potential(self, ctx: DiscountContext) -> Decimal:
        """Расчёт выгоды без изменения контекста (для сравнения)."""
        return Decimal('0')


class PercentDiscount(BaseDiscount):
    def __init__(self, percent, name):
        self.name = name
        self.stage = "percent"
        self.percent = Decimal(str(percent))

    def apply(self, ctx):
        amount = round_money(ctx.subtotal * self.percent / 100)
        if amount > 0:
            amount = min(amount, ctx.subtotal)
            ctx.subtotal -= amount
            return AppliedDiscount(self.name, amount, f"Скидка {self.percent}%")
        return None

    def get_potential(self, ctx):
        return round_money(ctx.subtotal * self.percent / 100)


class FixedDiscount(BaseDiscount):
    def __init__(self, value, threshold, name):
        self.name = name
        self.stage = "fixed"
        self.value = Decimal(str(value))
        self.threshold = Decimal(str(threshold))

    def apply(self, ctx):
        if ctx.subtotal >= self.threshold:
            amount = min(self.value, ctx.subtotal)
            ctx.subtotal -= amount
            return AppliedDiscount(self.name, amount, f"Скидка {self.value} от {self.threshold}")
        return None


class ThreeForTwoDiscount(BaseDiscount):
    def __init__(self, category, name):
        self.name = name
        self.stage = "three_for_two"
        self.category = category

    def apply(self, ctx):
        prices = []
        for item in ctx.order.items:
            if item.product.category == self.category:
                for _ in range(item.quantity):
                    prices.append(item.product.base_price)

        if len(prices) < 3:
            return None

        prices.sort()  # Сортируем, чтобы бесплатными стали самые дешёвые
        free_count = len(prices) // 3
        if free_count == 0:
            return None

        amount = round_money(sum(prices[:free_count]))
        if amount > 0:
            amount = min(amount, ctx.subtotal)
            ctx.subtotal -= amount
            return AppliedDiscount(self.name, amount, f"Категория {self.category}: {free_count} бесплатно")
        return None


class PromoCodeDiscount(BaseDiscount):
    def __init__(self, code, is_percent, value, expiry, name):
        self.name = name
        self.stage = "percent" if is_percent else "fixed"
        self.is_promo = True
        self.code = code
        self.is_percent = is_percent
        self.value = Decimal(str(value))

        # ИСПРАВЛЕНИЕ: принимаем и строку, и уже готовый объект date
        if expiry is None:
            self.expiry = None
        elif isinstance(expiry, date):
            self.expiry = expiry  # Уже объект date
        else:
            self.expiry = date.fromisoformat(expiry)  # Строка вида "2026-09-24"

    def is_valid(self, ctx):
        if ctx.promo_used: return False
        if ctx.order.promo_code != self.code: return False
        if self.expiry and ctx.order.created_at > self.expiry: return False
        return True

    def apply(self, ctx):
        if not self.is_valid(ctx):
            return None

        ctx.promo_used = True
        if self.is_percent:
            amount = round_money(ctx.subtotal * self.value / 100)
            reason = f"Промокод {self.code}: {self.value}%"
        else:
            amount = min(self.value, ctx.subtotal)
            reason = f"Промокод {self.code}: фикс. {self.value}"

        if amount > 0:
            amount = min(amount, ctx.subtotal)
            ctx.subtotal -= amount
            return AppliedDiscount(self.name, amount, reason)
        return None

    def get_potential(self, ctx):
        if self.is_valid(ctx) and self.is_percent:
            return round_money(ctx.subtotal * self.value / 100)
        return Decimal('0')


class LoyaltyDiscount(BaseDiscount):
    def __init__(self, percent, days, name):
        self.name = name
        self.stage = "percent"
        self.is_loyalty = True
        self.percent = Decimal(str(percent))
        self.days = int(days)

    def apply(self, ctx):
        last = ctx.order.customer.last_purchase_date
        if not last or last > ctx.order.created_at:
            return None

        days_diff = (ctx.order.created_at - last).days
        if 0 <= days_diff <= self.days:
            amount = round_money(ctx.subtotal * self.percent / 100)
            if amount > 0:
                amount = min(amount, ctx.subtotal)
                ctx.subtotal -= amount
                return AppliedDiscount(self.name, amount, f"Покупка была {days_diff} дн. назад")
        return None


class FirstOrderDiscount(BaseDiscount):
    def __init__(self, percent, name):
        self.name = name
        self.stage = "percent"
        self.is_first_order = True
        self.percent = Decimal(str(percent))

    def apply(self, ctx):
        if ctx.order.customer.is_first_order:
            amount = round_money(ctx.subtotal * self.percent / 100)
            if amount > 0:
                amount = min(amount, ctx.subtotal)
                ctx.subtotal -= amount
                return AppliedDiscount(self.name, amount, "Скидка на первый заказ 10%")
        return None


class FreeDeliveryDiscount(BaseDiscount):
    def __init__(self, threshold, name):
        self.name = name
        self.stage = "delivery"
        self.threshold = Decimal(str(threshold))

    def apply(self, ctx):
        # Строго больше порога
        if ctx.subtotal > self.threshold and ctx.delivery > 0:
            amount = ctx.delivery
            ctx.delivery = Decimal('0')
            return AppliedDiscount(self.name, amount, f"Бесплатная доставка > {self.threshold}")
        return None


# ==========================================
# 4. ФАБРИКА И СИНГЛТОН (Реестр)
# ==========================================
class DiscountFactory:
    """Создаёт объекты скидок по словарю конфигурации."""

    @staticmethod
    def create(config: Dict) -> BaseDiscount:
        t = config["type"]
        name = config.get("name", t)

        if t == "percent":
            return PercentDiscount(config["value"], name)
        elif t == "fixed":
            return FixedDiscount(config["value"], config["threshold"], name)
        elif t == "threeForTwo":
            return ThreeForTwoDiscount(config["category"], name)
        elif t == "loyalty":
            return LoyaltyDiscount(config["percent"], config["days"], name)
        elif t == "firstOrder":
            return FirstOrderDiscount(config["percent"], name)
        elif t == "freeDelivery":
            return FreeDeliveryDiscount(config["threshold"], name)
        elif t == "promoCode":
            return PromoCodeDiscount(config["code"], config["isPercent"], config["value"], config.get("expiry"), name)

        raise ValueError(f"Неизвестный тип скидки: {t}")


class DiscountRegistry:
    """Синглтон для хранения конфигурации. Не хранит состояние заказа."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._creators: Dict[str, Callable] = {}
        return cls._instance

    def register(self, discount_type: str, creator: Callable):
        self._creators[discount_type] = creator

    def create(self, discount_type: str, config: Dict) -> BaseDiscount:
        creator = self._creators.get(discount_type)
        if not creator:
            raise ValueError(f"Неизвестный тип скидки: {discount_type}")
        return creator(config)


# Инициализация реестра
registry = DiscountRegistry()
registry.register("percent", lambda c: PercentDiscount(Decimal(str(c['value'])), c['name']))
registry.register("fixed", lambda c: FixedDiscount(Decimal(str(c['value'])), Decimal(str(c['threshold'])), c['name']))
registry.register("threeForTwo", lambda c: ThreeForTwoDiscount(c['category'], c['name']))
registry.register("loyalty", lambda c: LoyaltyDiscount(Decimal(str(c['percent'])), c['days'], c['name']))
registry.register("firstOrder", lambda c: FirstOrderDiscount(Decimal(str(c['percent'])), c['name']))
registry.register("freeDelivery", lambda c: FreeDeliveryDiscount(Decimal(str(c['threshold'])), c['name']))
registry.register("promoCode", lambda c: PromoCodeDiscount(
    c['code'], c['isPercent'], Decimal(str(c['value'])),
    c.get('expiry'), c['name']
))


# ==========================================
# 5. ДВИЖОК РАСЧЁТА (БЕЗ isinstance!)
# ==========================================
class PricingEngine:
    def calculate(self, order: Order, discounts: List[BaseDiscount]) -> PricingResult:
        base_subtotal = sum(item.product.base_price * item.quantity for item in order.items)
        base_total = round_money(base_subtotal + order.delivery_cost)

        ctx = DiscountContext(
            order=order,
            subtotal=base_subtotal,
            delivery=order.delivery_cost,
            applied_discounts=[]
        )

        # Группируем по этапам через свойство stage (Полиморфизм, без isinstance)
        stages = {"three_for_two": [], "percent": [], "fixed": [], "delivery": []}
        for d in discounts:
            stages[d.stage].append(d)

        # Этап 1: 3 по цене 2
        for d in stages["three_for_two"]:
            res = d.apply(ctx)
            if res: ctx.applied_discounts.append(res)

        # Этап 2: Процентные (выбор лучшей между обычной и промокодом)
        best_discount = None
        max_amount = Decimal('0')

        for d in stages["percent"]:
            if d.is_promo or (not d.is_loyalty and not d.is_first_order):
                amount = d.get_potential(ctx)
                if amount > max_amount or (amount == max_amount and amount > 0 and d.is_promo):
                    max_amount = amount
                    best_discount = d

        if best_discount and max_amount > 0:
            res = best_discount.apply(ctx)
            if res: ctx.applied_discounts.append(res)

        # Этап 2.5: Лояльность и первый заказ (суммируются)
        for d in stages["percent"]:
            if d.is_loyalty or d.is_first_order:
                res = d.apply(ctx)
                if res: ctx.applied_discounts.append(res)

        # Этап 3: Фиксированные (включая фиксированные промокоды)
        for d in stages["fixed"]:
            res = d.apply(ctx)
            if res: ctx.applied_discounts.append(res)

        # Этап 4: Доставка
        for d in stages["delivery"]:
            res = d.apply(ctx)
            if res: ctx.applied_discounts.append(res)

        final_total = round_money(max(Decimal('0'), ctx.subtotal + ctx.delivery))

        return PricingResult(base_total, ctx.applied_discounts, final_total)


# ==========================================
# 6. ТЕСТЫ С ПОДРОБНЫМ ВЫВОДОМ (ВХОД / ВЫХОД)
# ==========================================
class TestDiscountEngine(unittest.TestCase):
    def setUp(self):
        self.engine = PricingEngine()
        self.today = date(2026, 9, 24)

    def _create_order(self, items, delivery=Decimal('500'), is_first=False, last_purchase=None, promo=None):
        customer = Customer(id="c1", last_purchase_date=last_purchase, is_first_order=is_first)
        return Order(id="o1", customer=customer, items=items, promo_code=promo,
                     created_at=self.today, delivery_cost=delivery)

    def _print_separator(self, test_name):
        print("\n" + "=" * 75)
        print(f"ТЕСТ: {test_name}")
        print("=" * 75)

    def _print_input(self, items, discounts, delivery, promo, last_purchase, is_first):
        print("\n📥 ВХОД:")
        print("  Товары:")
        if items:
            for item in items:
                total = item.product.base_price * item.quantity
                print(f"    - {item.product.name}: {item.quantity} шт × {item.product.base_price} = {total}")
        else:
            print("    (пустая корзина)")
        print(f"  Доставка: {delivery}")
        print(f"  Промокод: {promo if promo else 'нет'}")
        print(f"  Первый заказ: {'Да' if is_first else 'Нет'}")
        print(f"  Последняя покупка: {last_purchase if last_purchase else 'нет'}")
        print("  Активные скидки:")
        for d in discounts:
            print(f"    - {d.name}")

    def _print_output(self, result):
        print("\n📤 ВЫХОД:")
        print(f"  Базовая сумма: {result.base_total}")
        print("  Применённые скидки:")
        if result.applied_discounts:
            for d in result.applied_discounts:
                print(f"    ✅ {d.name}: -{d.amount} ({d.reason})")
        else:
            print("    ❌ (нет)")
        print(f"  💰 ИТОГО К ОПЛАТЕ: {result.final_total}")

    # --- 1. Каждая стратегия (позитив + негатив) ---

    def test_01_percent_apply(self):
        self._print_separator("Процентная скидка 10% - применяется")
        items = [CartItem(Product("1", "Товар", Decimal('1000'), "Cat"), 2)]
        discounts = [PercentDiscount(Decimal('10'), "10%")]
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500')), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: 2000 - 200 (10%) + 500 = 2300.00")
        self.assertEqual(res.final_total, Decimal('2300.00'))
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_02_percent_not_apply(self):
        self._print_separator("Процентная скидка 0% - не применяется")
        items = [CartItem(Product("1", "Товар", Decimal('1000'), "Cat"), 2)]
        discounts = [PercentDiscount(Decimal('0'), "0%")]
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500')), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: Скидка 0% не применится")
        self.assertEqual(len(res.applied_discounts), 0)
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_03_fixed_apply(self):
        self._print_separator("Фиксированная скидка 500 от 1000 - применяется")
        items = [CartItem(Product("1", "Товар", Decimal('1000'), "Cat"), 2)]
        discounts = [FixedDiscount(Decimal('500'), Decimal('1000'), "500 от 1000")]
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500')), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: 2000 - 500 + 500 = 2000.00")
        self.assertEqual(res.final_total, Decimal('2000.00'))
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_04_fixed_not_apply(self):
        self._print_separator("Фиксированная скидка 500 от 1000 - не применяется")
        items = [CartItem(Product("1", "Товар", Decimal('100'), "Cat"), 2)]
        discounts = [FixedDiscount(Decimal('500'), Decimal('1000'), "500 от 1000")]
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500')), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: 200 < 1000, скидка не применится")
        self.assertEqual(len(res.applied_discounts), 0)
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_05_3_for_2_apply(self):
        self._print_separator("3 по цене 2 - применяется")
        items = [CartItem(Product("1", "Книга", Decimal('300'), "Книги"), 3)]
        discounts = [ThreeForTwoDiscount("Книги", "3 по цене 2")]
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500')), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: 900 - 300 (1 бесплатная) + 500 = 1100.00")
        self.assertEqual(res.final_total, Decimal('1100.00'))
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_06_3_for_2_not_apply(self):
        self._print_separator("3 по цене 2 - не применяется (всего 2 товара)")
        items = [CartItem(Product("1", "Книга", Decimal('300'), "Книги"), 2)]
        discounts = [ThreeForTwoDiscount("Книги", "3 по цене 2")]
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500')), discounts)
        self._print_output(res)
        print(f"\n ОЖИДАЕМО: 2 < 3, скидка не применится")
        self.assertEqual(len(res.applied_discounts), 0)
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_07_loyalty_apply(self):
        self._print_separator("Лояльность 5% - применяется (15 дней назад)")
        items = [CartItem(Product("1", "Товар", Decimal('1000'), "Cat"), 1)]
        last_date = date(2026, 9, 9)
        discounts = [LoyaltyDiscount(Decimal('5'), 30, "Лояльность")]
        self._print_input(items, discounts, Decimal('500'), None, last_date, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500'), last_purchase=last_date),
                                    discounts)
        self._print_output(res)
        print(f"\n ОЖИДАЕМО: 1000 - 50 (5%) + 500 = 1450.00")
        self.assertEqual(len(res.applied_discounts), 1)
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_08_loyalty_not_apply(self):
        self._print_separator("Лояльность 5% - не применяется (60 дней назад)")
        items = [CartItem(Product("1", "Товар", Decimal('1000'), "Cat"), 1)]
        last_date = date(2026, 7, 26)
        discounts = [LoyaltyDiscount(Decimal('5'), 30, "Лояльность")]
        self._print_input(items, discounts, Decimal('500'), None, last_date, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500'), last_purchase=last_date),
                                    discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: 60 > 30 дней, скидка не применится")
        self.assertEqual(len(res.applied_discounts), 0)
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_09_first_order_apply(self):
        self._print_separator("Первый заказ 10% - применяется")
        items = [CartItem(Product("1", "Товар", Decimal('1000'), "Cat"), 1)]
        discounts = [FirstOrderDiscount(Decimal('10'), "Первый заказ")]
        self._print_input(items, discounts, Decimal('500'), None, None, True)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500'), is_first=True), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: 1000 - 100 (10%) + 500 = 1400.00")
        self.assertEqual(len(res.applied_discounts), 1)
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_10_first_order_not_apply(self):
        self._print_separator("Первый заказ 10% - не применяется")
        items = [CartItem(Product("1", "Товар", Decimal('1000'), "Cat"), 1)]
        discounts = [FirstOrderDiscount(Decimal('10'), "Первый заказ")]
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500'), is_first=False), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: isFirstOrder=False, скидка не применится")
        self.assertEqual(len(res.applied_discounts), 0)
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_11_free_delivery_apply(self):
        self._print_separator("Бесплатная доставка > 3000 - применяется")
        items = [CartItem(Product("1", "Товар", Decimal('4000'), "Cat"), 1)]
        discounts = [FreeDeliveryDiscount(Decimal('3000'), "Бесплатная доставка")]
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500')), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: 4000 > 3000, доставка бесплатна. Итого: 4000.00")
        self.assertEqual(res.final_total, Decimal('4000.00'))
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_12_free_delivery_not_apply(self):
        self._print_separator("Бесплатная доставка > 5000 - не применяется (равно)")
        items = [CartItem(Product("1", "Товар", Decimal('5000'), "Cat"), 1)]
        discounts = [FreeDeliveryDiscount(Decimal('5000'), "Бесплатная доставка")]
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500')), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: 5000 не строго больше 5000, доставка платная")
        self.assertEqual(len(res.applied_discounts), 0)
        print("✓ ТЕСТ ПРОЙДЕН")

    # --- 2. Комбинации ---

    def test_13_3_for_2_and_percent(self):
        self._print_separator("3 по цене 2 + 10% (последовательное применение)")
        items = [CartItem(Product("1", "Книга", Decimal('1000'), "Книги"), 3)]
        discounts = [ThreeForTwoDiscount("Книги", "3 по цене 2"), PercentDiscount(Decimal('10'), "10%")]
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500')), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: 3000 - 1000 (3по2) = 2000; 2000 - 200 (10%) = 1800; + 500 = 2300.00")
        self.assertEqual(res.final_total, Decimal('2300.00'))
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_14_promo_vs_percent_choose_max(self):
        self._print_separator("Промокод 15% vs скидка 10% - выбирается промокод")
        items = [CartItem(Product("1", "Товар", Decimal('1000'), "Cat"), 2)]
        discounts = [PercentDiscount(Decimal('10'), "10%"),
                     PromoCodeDiscount("PROMO15", True, Decimal('15'), None, "Промокод PROMO15")]
        self._print_input(items, discounts, Decimal('500'), "PROMO15", None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500'), promo="PROMO15"), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: Выбран промокод 15% (300) вместо 10% (200). Итого: 2200.00")
        self.assertEqual(res.applied_discounts[0].name, "Промокод PROMO15")
        self.assertEqual(res.final_total, Decimal('2200.00'))
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_15_promo_vs_percent_equal_choose_promo(self):
        self._print_separator("Промокод 10% vs скидка 10% - при равенстве промокод")
        items = [CartItem(Product("1", "Товар", Decimal('1000'), "Cat"), 2)]
        discounts = [PercentDiscount(Decimal('10'), "10%"),
                     PromoCodeDiscount("PROMO10", True, Decimal('10'), None, "Промокод PROMO10")]
        self._print_input(items, discounts, Decimal('500'), "PROMO10", None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500'), promo="PROMO10"), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: При равенстве выбран промокод. Итого: 2300.00")
        self.assertEqual(res.applied_discounts[0].name, "Промокод PROMO10")
        print("✓ ТЕСТ ПРОЙДЕН")

    # --- 3. Границы и безопасность ---

    def test_16_lower_bound_no_negative(self):
        self._print_separator("Нижняя граница - скидка не уходит в минус")
        items = [CartItem(Product("1", "Товар", Decimal('100'), "Cat"), 1)]
        discounts = [FixedDiscount(Decimal('500'), Decimal('50'), "500 от 50")]
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500')), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: Скидка 500 ограничена суммой 100. Итого: 0 + 500 = 500.00")
        self.assertEqual(res.final_total, Decimal('500.00'))
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_17_rounding_half_to_even(self):
        self._print_separator("Округление half to even")
        items = [CartItem(Product("1", "Товар", Decimal('10.015'), "Cat"), 2)]  # 20.03
        discounts = [PercentDiscount(Decimal('10'), "10%")]  # 2.003 -> 2.00
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500')), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: 20.03 × 10% = 2.003 → округление до 2.00 (half to even)")
        self.assertEqual(res.applied_discounts[0].amount, Decimal('2.00'))
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_18_loyalty_boundary_30_days(self):
        self._print_separator("Лояльность - граница ровно 30 дней")
        items = [CartItem(Product("1", "Товар", Decimal('1000'), "Cat"), 1)]
        last_date = date(2026, 8, 25)  # Ровно 30 дней
        discounts = [LoyaltyDiscount(Decimal('5'), 30, "Лояльность")]
        self._print_input(items, discounts, Decimal('500'), None, last_date, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500'), last_purchase=last_date),
                                    discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: Ровно 30 дней - граница включительна. Скидка применена.")
        self.assertEqual(len(res.applied_discounts), 1)
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_19_promo_expiry_boundary(self):
        self._print_separator("Промокод - граница срока действия (включительно)")
        items = [CartItem(Product("1", "Товар", Decimal('1000'), "Cat"), 1)]
        expiry = date(2026, 9, 24)  # Сегодня - ИСПРАВЛЕНО: передаём объект date, а не строку
        discounts = [PromoCodeDiscount("EXPIRY", True, Decimal('10'), expiry, "Промокод EXPIRY")]
        self._print_input(items, discounts, Decimal('500'), "EXPIRY", None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500'), promo="EXPIRY"), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: Сегодня = последний день, промокод действителен")
        self.assertEqual(len(res.applied_discounts), 1)
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_20_fixed_threshold_boundary(self):
        self._print_separator("Фиксированная скидка - граница порога (>=)")
        items = [CartItem(Product("1", "Товар", Decimal('3000'), "Cat"), 1)]
        discounts = [FixedDiscount(Decimal('100'), Decimal('3000'), "100 от 3000")]
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500')), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: 3000 >= 3000, скидка применена. Итого: 2900 + 500 = 3400.00")
        self.assertEqual(res.final_total, Decimal('3400.00'))
        print("✓ ТЕСТ ПРОЙДЕН")

    # --- 4. Дополнительные (рекомендуемые) ---

    def test_21_breakdown_correct(self):
        self._print_separator("Breakdown - корректность всех записей")
        items = [CartItem(Product("1", "Книга", Decimal('1000'), "Книги"), 3)]
        discounts = [ThreeForTwoDiscount("Книги", "3 по цене 2"), PercentDiscount(Decimal('10'), "10%"),
                     LoyaltyDiscount(Decimal('5'), 30, "Лояльность")]
        last_date = date(2026, 9, 10)  # 14 дней назад
        self._print_input(items, discounts, Decimal('500'), None, last_date, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500'), last_purchase=last_date),
                                    discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: Порядок: 3по2 → 10% → Лояльность. Итого: 2210.00")
        self.assertEqual(len(res.applied_discounts), 3)
        self.assertEqual(res.applied_discounts[0].name, "3 по цене 2")
        self.assertEqual(res.applied_discounts[1].name, "10%")
        self.assertEqual(res.applied_discounts[2].name, "Лояльность")
        self.assertEqual(res.final_total, Decimal('2210.00'))
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_22_3_for_2_different_prices(self):
        self._print_separator("3 по цене 2 - бесплатными самые дешёвые")
        items = [CartItem(Product("1", "Книга дорогая", Decimal('1000'), "Книги"), 1),
                 CartItem(Product("2", "Книга средняя", Decimal('500'), "Книги"), 1),
                 CartItem(Product("3", "Книга дешёвая", Decimal('300'), "Книги"), 1)]
        discounts = [ThreeForTwoDiscount("Книги", "3 по цене 2")]
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500')), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: Бесплатная самая дешёвая (300). Итого: 1500 + 500 = 2000.00")
        self.assertEqual(res.applied_discounts[0].amount, Decimal('300.00'))
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_23_empty_cart(self):
        self._print_separator("Пустая корзина")
        items = []
        discounts = [PercentDiscount(Decimal('10'), "10%")]
        self._print_input(items, discounts, Decimal('500'), None, None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500')), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: 0 товаров, только доставка = 500.00")
        self.assertEqual(res.final_total, Decimal('500.00'))
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_24_unknown_promo(self):
        self._print_separator("Неизвестный промокод")
        items = [CartItem(Product("1", "Товар", Decimal('1000'), "Cat"), 1)]
        discounts = [PromoCodeDiscount("REAL10", True, Decimal('10'), None, "Промокод REAL10")]
        self._print_input(items, discounts, Decimal('500'), "FAKE", None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500'), promo="FAKE"), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: Промокод FAKE не найден, скидка не применена")
        self.assertEqual(len(res.applied_discounts), 0)
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_25_expired_promo(self):
        self._print_separator("Просроченный промокод")
        items = [CartItem(Product("1", "Товар", Decimal('1000'), "Cat"), 1)]
        expiry = date(2026, 9, 20)  # 4 дня назад - ИСПРАВЛЕНО: передаём объект date
        discounts = [PromoCodeDiscount("OLD", True, Decimal('10'), expiry, "Промокод OLD")]
        self._print_input(items, discounts, Decimal('500'), "OLD", None, False)
        res = self.engine.calculate(self._create_order(items, delivery=Decimal('500'), promo="OLD"), discounts)
        self._print_output(res)
        print(f"\n🎯 ОЖИДАЕМО: Промокод просрочен (20.09 < 24.09)")
        self.assertEqual(len(res.applied_discounts), 0)
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_26_all_compatible_discounts(self):
        self._print_separator("Все совместимые скидки вместе")
        items = [CartItem(Product("1", "Книга", Decimal('1000'), "Книги"), 3)]
        discounts = [
            ThreeForTwoDiscount("Книги", "3 по цене 2"),
            PercentDiscount(Decimal('10'), "10%"),
            LoyaltyDiscount(Decimal('5'), 30, "Лояльность"),
            FirstOrderDiscount(Decimal('10'), "Первый заказ"),
            FreeDeliveryDiscount(Decimal('1000'), "Бесплатная доставка")
        ]
        last_date = date(2026, 9, 10)
        self._print_input(items, discounts, Decimal('500'), None, last_date, True)
        res = self.engine.calculate(
            self._create_order(items, delivery=Decimal('500'), last_purchase=last_date, is_first=True), discounts)
        self._print_output(res)
        print(
            f"\n🎯 ОЖИДАЕМО: 3000 - 1000 = 2000; 2000 - 200 = 1800; 1800 - 90 = 1710; 1710 - 171 = 1539; доставка 0. Итого: 1539.00")
        self.assertEqual(res.final_total, Decimal('1539.00'))
        self.assertEqual(len(res.applied_discounts), 5)
        print("✓ ТЕСТ ПРОЙДЕН")

    def test_27_factory_invalid_config(self):
        self._print_separator("Некорректная конфигурация фабрики")
        print("\n📥 ВХОД: type='invalid_type'")
        try:
            registry.create("invalid_type", {})
            print("❌ ОШИБКА: Исключение не выброшено!")
            self.fail("Factory должен выбросить исключение")
        except ValueError as e:
            print(f"\n ВЫХОД: Исключение ValueError: {e}")
            print(f"\n🎯 ОЖИДАЕМО: ValueError с сообщением о неизвестном типе")
            self.assertIn("Неизвестный тип скидки", str(e))
            print("✓ ТЕСТ ПРОЙДЕН")


# ==========================================
# 7. МЕНЮ И ЗАПУСК
# ==========================================
def run_demo():
    print("\n" + "=" * 75)
    print("ДЕМОНСТРАЦИЯ РАСЧЁТА ЗАКАЗА")
    print("=" * 75)

    config = [
        {"type": "percent", "value": 10, "name": "Осенняя распродажа"},
        {"type": "threeForTwo", "category": "Книги", "name": "3 по цене 2"},
        {"type": "loyalty", "percent": 5, "days": 30, "name": "Лояльность"},
        {"type": "freeDelivery", "threshold": 3000, "name": "Бесплатная доставка"}
    ]
    discounts = [registry.create(c['type'], c) for c in config]

    book = Product("p1", "Python для начинающих", Decimal('1000'), "Книги")
    mug = Product("p2", "Кружка", Decimal('500'), "Посуда")
    customer = Customer(id="c1", last_purchase_date=date(2026, 9, 10), is_first_order=False)
    order = Order(
        id="ord_123", customer=customer,
        items=[CartItem(book, 3), CartItem(mug, 2)],
        promo_code=None, created_at=date(2026, 9, 24), delivery_cost=Decimal('600')
    )

    print("\n📥 ВХОДНЫЕ ДАННЫЕ:")
    for item in order.items:
        print(
            f"  - {item.product.name}: {item.quantity} шт × {item.product.base_price} = {item.product.base_price * item.quantity}")
    print(f"  Доставка: {order.delivery_cost}")
    print(f"  Последняя покупка: {order.customer.last_purchase_date}")

    engine = PricingEngine()
    result = engine.calculate(order, discounts)

    print("\n📤 РЕЗУЛЬТАТ (Breakdown):")
    print(f"  Базовая сумма: {result.base_total}")
    for d in result.applied_discounts:
        print(f"  ✅ {d.name}: -{d.amount} ({d.reason})")
    print(f"  💰 ИТОГО К ОПЛАТЕ: {result.final_total}")
    print("=" * 75 + "\n")


def run_tests():
    print("\n" + "=" * 75)
    print("ЗАПУСК UNIT-ТЕСТОВ (полный набор по требованиям)")
    print("=" * 75)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestDiscountEngine)
    # verbosity=0 скрывает стандартный вывод unittest, оставляя только наши красивые print
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=0)
    result = runner.run(suite)
    print("\n" + "=" * 75)
    print(f"ВСЕГО ТЕСТОВ: {result.testsRun}")
    print(f"ОШИБОК: {len(result.errors)}")
    print(f"ПАДЕНИЙ: {len(result.failures)}")
    print("=" * 75 + "\n")


def main_menu():
    while True:
        print("\n" + "=" * 75)
        print(" МАРКЕТПЛЕЙС 'ШТУЧКИ-ДРЮЧКИ' - ДВИЖОК СКИДОК")
        print("=" * 75)
        print(" 1. Запустить демонстрацию расчёта")
        print(" 2. Запустить все тесты (27 тестов с подробным выводом ВХОД/ВЫХОД)")
        print(" 3. Выход")
        print("=" * 75)

        choice = input("Выберите действие (1-3): ").strip()

        if choice == '1':
            run_demo()
        elif choice == '2':
            run_tests()
        elif choice == '3':
            print("\nВыход из программы. Удачи!\n")
            break
        else:
            print("\n⚠️ Неверный ввод. Пожалуйста, выберите 1, 2 или 3.")


if __name__ == "__main__":
    main_menu()