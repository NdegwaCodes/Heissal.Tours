import { CatalogueResource } from "@/components/app/catalogue-resource";
import { FUEL_PRICES } from "@/lib/catalogue";

export default function Page() {
  return <CatalogueResource spec={FUEL_PRICES} />;
}
