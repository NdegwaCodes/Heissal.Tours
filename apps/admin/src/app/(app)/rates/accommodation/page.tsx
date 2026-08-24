import { CatalogueResource } from "@/components/app/catalogue-resource";
import { ACCOMMODATION_RATES } from "@/lib/catalogue";

export default function Page() {
  return <CatalogueResource spec={ACCOMMODATION_RATES} />;
}
