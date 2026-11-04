"""Static surface checks for the final 0.3.0 write resources. Never run."""

from apexai_shop import AsyncShop, Shop, WriteResult, models


def sync_writes(shop: Shop, change: models.RepriceChange) -> None:
    broadcast: WriteResult[models.BroadcastInfo] = shop.broadcasts.send("News")
    cancelled: WriteResult[models.BroadcastInfo] = shop.broadcasts.cancel(1)
    bundle: WriteResult[models.ItemDetail] = shop.bundles.create(1, "Pack", 500)
    components: WriteResult[models.Bundle] = shop.bundles.update(2, add_item_id=1)
    variant: WriteResult[models.Variant] = shop.variants.add(1, "Personal", 300)
    variant = shop.variants.update(2, version="0123456789abcdef", active=True)
    requested: WriteResult[models.FileRequested] = shop.items.request_file(1)
    repriced: WriteResult[models.RepriceResult] = shop.items.reprice([change])
    print(broadcast, cancelled, bundle, components, variant, requested, repriced)


async def async_writes(shop: AsyncShop, change: models.RepriceChange) -> None:
    broadcast: WriteResult[models.BroadcastInfo] = await shop.broadcasts.send("News")
    cancelled: WriteResult[models.BroadcastInfo] = await shop.broadcasts.cancel(1)
    bundle: WriteResult[models.ItemDetail] = await shop.bundles.create(1, "Pack", 500)
    components: WriteResult[models.Bundle] = await shop.bundles.update(2, remove_item_id=1)
    variant: WriteResult[models.Variant] = await shop.variants.add(1, "Personal", 300)
    variant = await shop.variants.update(2, version="0123456789abcdef", active=True)
    requested: WriteResult[models.FileRequested] = await shop.items.request_file(1)
    repriced: WriteResult[models.RepriceResult] = await shop.items.reprice([change])
    print(broadcast, cancelled, bundle, components, variant, requested, repriced)
