import os
import csv


############################################################################################################
# Name: calculate_accuracy(accuracy_csv)
# Description: Function to get the overall accuracy from the accuracy CSV file.
# The accuracy_csv parameter is the path to the accuracy CSV file.
############################################################################################################
def calculate_accuracy(accuracy_csv):
    # Read the accuracy CSV file
    with open(accuracy_csv, 'r') as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        if rows:
            last_row = rows[12]
            accuracy_str = last_row.get('Monthly Accuracy', '')
            if accuracy_str:
                try:
                    accuracy = float(accuracy_str)
                    if accuracy > 0:
                        return accuracy, False
                    else:
                        return accuracy, True
                except ValueError:
                    pass
    return None, True

############################################################################################################
# Name: get_overall_accuracy(clean_predicted_reports_dir, output_csv)
# Description: Function to iterate over the clean predicted reports and calculate the overall accuracy.
# The clean_predicted_reports_dir parameter is the path to the directory containing the clean predicted reports.
# The output_csv parameter is the path to the output CSV file.
############################################################################################################
def get_overall_accuracy(clean_predicted_reports_dir, output_csv):
    total_accuracy = 0
    building_count = 0
    building_accuracies = []
    excluded_accuracies = []

    
    # Iterate over the clean predicted reports
    for building_dir in os.listdir(clean_predicted_reports_dir):
        building_path = os.path.join(clean_predicted_reports_dir, building_dir)
        if os.path.isdir(building_path):
            results_csv = os.path.join(building_path, 'electricity_analyzed_results.csv')
            if os.path.isfile(results_csv):
                last_accuracy, excluded = calculate_accuracy(results_csv)
                if last_accuracy is not None and not excluded:
                    total_accuracy += last_accuracy
                    building_count += 1
                    building_accuracies.append([building_dir, last_accuracy, ''])  # Store building name and accuracy
                else:
                    excluded_accuracies.append([building_dir, '', last_accuracy])
    
    # Calculate and print the overall accuracy
    if building_count > 0:
        overall_accuracy = total_accuracy / building_count
        print(f"Overall accuracy: {overall_accuracy}")
    else:
        print("No valid accuracies found.")
        overall_accuracy = None

    
    # Write building accuracies to the output CSV
    with open(output_csv, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['Building Name', 'Building Accuracy', 'Excluded Accuracy'])
        writer.writerows(building_accuracies + excluded_accuracies)
        if overall_accuracy is not None:
            writer.writerow(['Overall Accuracy', overall_accuracy, ''])


############################################################################################################
# Main script execution: Test the overall accuracy calculation
############################################################################################################
if __name__ == "__main__":
    clean_predicted_reports_dir = ''
    output_csv = ''
    get_overall_accuracy(clean_predicted_reports_dir, output_csv)
