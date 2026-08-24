import { CatalogueResource } from "@/components/app/catalogue-resource";
import { VEHICLES } from "@/lib/catalogue";

export default function Page() {
  return <CatalogueResource spec={VEHICLES} />;
}
